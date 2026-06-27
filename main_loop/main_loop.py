import time
import asyncio
import json
import queue
from typing import Optional
import threading
from datetime import datetime, timezone

from files.main_loop import gen_response
from files.main_loop import turn_control
from files.main_loop.infer_emotion import infer_emotion, DEFAULT_EMOTIONS
from files.memory.chromadb import (store_user_memory_main, store_ai_memory_for_chatter_main, store_ai_global_memory_main, recall_chatter_messages_main, recall_ai_about_chatter_main, recall_ai_global_main, random_memories_main, format_memories)
from files.moderation.check_for_bad_words import check_for_bad_words, decode_bad_words
from files.moderation.check_for_spam import is_spam_message
from files.moderation.text_cleaner import split_message, cleaned_chat_pairings
from files.fine_tune.fine_tune_creator import create_chat_pair
from files.system_setup.settings import USERDATA_DIR, get_settings
from files.system_setup.json_utils import load_json_safe
from files.tts.narrator.backends.narrator import get_narrator
from files.closed_captions import clear_caption, caption_coordinator, SOURCE_NARRATOR, SOURCE_USER, SOURCE_LLM
from files.vtube_studio.hotkeys import VTSHotkeyManager
from files.system_setup.system_logger import Logger
from files.ui.pages.logs import LogManager

CENSOR_PLACEHOLDER = "FILTERED"
BAD_WORDS = decode_bad_words()
UNKNOWN_USER_ID = "unknown_user"
message_queue: "queue.Queue[tuple[str, bool, Optional[queue.Queue]]]" = queue.Queue()
async_loop = None
_vts_manager: Optional[VTSHotkeyManager] = None
_turn_active = threading.Event()
SAVED_CHAT = USERDATA_DIR / "conversation_context.json"
LAST_TURNS = 3
_persistent_context_injected = False

def is_turn_active() -> bool:
    return _turn_active.is_set()

def load_context() -> list[dict]:
    data = load_json_safe(
        SAVED_CHAT,
        {"turns": []},
        dict,
        logger=lambda message: Logger.warn(f"Failed to load conversation context: {message}"),
    )
    turns = data.get("turns", []) if isinstance(data, dict) else []
    return turns if isinstance(turns, list) else []

def save_context(turns: list[dict]) -> None:
    try:
        SAVED_CHAT.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "turns": turns[-LAST_TURNS:],
        }
        with SAVED_CHAT.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as e:
        Logger.warn(f"Failed to save conversation context: {e}")

def _remember_completed_turn(*, source: str, speaker: str, user_text: str, assistant_text: str) -> None:
    user_text = (user_text or "").strip()
    assistant_text = (assistant_text or "").strip()
    if not user_text or not assistant_text:
        return
    if assistant_text == CENSOR_PLACEHOLDER or assistant_text.startswith(("Error:", "Turn interrupted")):
        return

    turns = load_context()
    turns.append({
        "source": source or "chat",
        "speaker": speaker or UNKNOWN_USER_ID,
        "user": user_text,
        "assistant": assistant_text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_context(turns)

def context_injection(turns: list[dict]) -> str:
    if not turns:
        return ""
    lines = [
        "Recent Conversation Context:",
        "These are the last completed turns from before the current app session. Use them only to preserve continuity.",
    ]
    for turn in turns[-LAST_TURNS:]:
        speaker = str(turn.get("speaker") or "User").strip() or "User"
        user_text = str(turn.get("user") or "").strip()
        assistant_text = str(turn.get("assistant") or "").strip()
        if user_text:
            lines.append(f"{speaker}: {user_text}")
        if assistant_text:
            lines.append(f"Assistant: {assistant_text}")
    return "\n".join(lines)


def prepend_contetx(llm_input: str) -> str:
    global _persistent_context_injected
    if _persistent_context_injected:
        return llm_input
    _persistent_context_injected = True
    context = context_injection(load_context())
    if not context:
        return llm_input
    _log_info("Injected persisted conversation context into first turn.")
    return f"{context}\n\nCurrent Message:\n{llm_input}"

def context_snapshot() -> None:
    turns = load_context()
    save_context(turns)

def get_vts_manager() -> Optional[VTSHotkeyManager]:
    return _vts_manager

def set_vts_manager(manager: VTSHotkeyManager) -> None:
    global _vts_manager
    _vts_manager = manager

def _log_info(text: str) -> None:
    try:
        LogManager.info(text)
    except Exception:
        pass

def _log_perf(label: str, duration_ms: float, detail: str = "") -> None:
    try:
        LogManager.perf(label, duration_ms=duration_ms, detail=detail)
    except Exception:
        pass

def _log_error(text: str, *, exc_info: bool = False) -> None:
    try:
        LogManager.error(text, exc_info=exc_info)
    except Exception:
        pass

def _log_suggestion(text: str) -> None:
    try:
        if hasattr(LogManager, "moderation"):
            LogManager.suggest(text)
        else:
            LogManager.record("MODERATION", text)
    except Exception:
        pass

def finish_turn(resp_q: Optional[queue.Queue], response: Optional[str]) -> None:
    try:
        if resp_q:
            resp_q.put(response or "")
    except Exception as e:
        Logger.warn(f"Failed response queue: {e}")
        _log_error(f"Failed response queue: {e}", exc_info=True)
    finally:
        Logger.clear_turn_id()

def turn_start(source: str, turn_id: str | None, speaker: str, text: str) -> None:
    if source != "twitch" or not turn_id:
        return
    ui = getattr(Logger, "ui_ref", None)
    if ui is None or not hasattr(ui, "outside_chat"):
        return
    try:
        ui.after(0, ui.outside_chat, turn_id, speaker, text, source)
    except Exception as e:
        Logger.warn(f"Failed to render Twitch turn start: {e}")

def turn_finish(source: str, turn_id: str | None, response: Optional[str]) -> None:
    if source != "twitch" or not turn_id:
        return
    ui = getattr(Logger, "ui_ref", None)
    if ui is None or not hasattr(ui, "outside_chat_finish"):
        return
    try:
        ui.after(0, ui.outside_chat_finish, turn_id, response or "", source)
    except Exception as e:
        Logger.warn(f"Failed to render Twitch turn finish: {e}")

def _live_memory_max_distance() -> float:
    try:
        value = float(get_settings("live_memory_recall_max_distance") or 1.0)
        return max(0.0, value)
    except Exception:
        return 1.0

def _incoming_caption_text(speaker: str, text: str, source: str) -> str:
    text = (text or "").strip()
    speaker = (speaker or "").strip()
    if not text:
        return ""
    if source in ("manual", "stt"):
        return text
    if not speaker or speaker == UNKNOWN_USER_ID:
        return text
    if text.lower().startswith(f"{speaker.lower()}:"):
        return text
    return f"{speaker}: {text}"

async def recall_memories_total(query: str, user_id: str, n_results: int = 3) -> list:
    max_distance = _live_memory_max_distance()
    chatter_mems = await recall_chatter_messages_main(
        query,
        user_id=user_id,
        n_results=n_results,
        max_distance=max_distance,
    )
    ai_chatter_mems = await recall_ai_about_chatter_main(
        query,
        user_id=user_id,
        n_results=n_results,
        max_distance=max_distance,
    )
    ai_global_mems = await recall_ai_global_main(
        query,
        n_results=n_results,
        max_distance=max_distance,
    )
    seen = set()
    combined = []
    for mem in chatter_mems + ai_chatter_mems + ai_global_mems:
        text = mem.get("text", "")
        if text not in seen:
            seen.add(text)
            combined.append(mem)
    return combined

async def _wait_for_playback(playback) -> None:
    if not isinstance(playback, threading.Thread):
        return
    while playback.is_alive():
        if turn_control.is_interrupted():
            gen_response.stop_tts()
            await asyncio.to_thread(playback.join, 0.25)
            raise turn_control.TurnInterrupted()
        await asyncio.to_thread(playback.join, 0.1)

def _read_emotion_mode() -> str:
    try:
        from files.system_setup.settings import get_bool_setting
        raw = get_settings("vts_emotion_mode")
        if raw in ("", None):
            legacy = get_bool_setting(
                "enable_vts_emotion_inference", default=True
            )
            return "llm" if legacy else "off"
        mode = str(raw).strip().lower()
        if mode in ("llm", "heuristic", "off"):
            return mode
    except Exception:
        pass
    return "llm"

async def start_emotion_inf(response_text: str) -> None:
    manager = _vts_manager
    if manager is None:
        return

    mode = _read_emotion_mode()
    if mode == "off":
        return
    try:
        mapping = manager.load_vts_mapping()
        mapped_emotions = [
            str(name).strip().lower()
            for name in mapping.get("emotions", {}).keys()
            if str(name).strip()]

        if len(mapped_emotions) >= 2:
            emotion_list = mapped_emotions
        else:
            emotion_list = DEFAULT_EMOTIONS

    except Exception as e:
        Logger.warn(f"Failed to read VTS mapping: {e}")
        _log_error(f"Failed to read VTS mapping: {e}", exc_info=True)
        emotion_list = DEFAULT_EMOTIONS

    character_name = (
        get_settings("character_name")
        or get_settings("active_novelai_character")
        or get_settings("username")
        or "Character"
    )

    t0 = time.perf_counter()
    try:
        emotion = await infer_emotion(
            response_text=response_text,
            emotion_list=emotion_list,
            character_name=character_name,
            timeout_seconds=6.0,
            mode=mode,
        )
    except Exception as e:
        Logger.warn(f"Inference error: {e}")
        _log_error(f"Inference error: {e}", exc_info=True)
        return
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        _log_perf(
            "VTS emotion inference",
            elapsed_ms,
            detail=f"mode={mode}, response_chars={len(response_text or '')}",
        )

    if not emotion:
        return

    try:
        mapping = manager.load_vts_mapping()
        mapped = mapping.get("emotions", {})

        if emotion not in mapped:
            Logger.warn(
                f"Inferred '{emotion}', but no VTS hotkey mapping exists for it yet."
            )
            _log_info(f"VTS emotion inferred but unmapped: {emotion}")
            manager.apply_emotion_to_idle(emotion)
            return

        await manager.set_emotion_and_animate_talking(emotion)
        Logger.print(f"Triggered VTS emotion: {emotion}")
        _log_info(f"Triggered VTS emotion: {emotion}")

    except Exception as e:
        Logger.warn(f"VTS trigger error: {e}")
        _log_error(f"VTS trigger error: {e}", exc_info=True)


async def main_loop():
    while True:
        raw = await asyncio.to_thread(message_queue.get)
        if raw is None:
            message_queue.task_done()
            break

        if isinstance(raw, tuple):
            if len(raw) == 4:
                message, needs_llm, resp_q, meta = raw
            elif len(raw) == 3:
                message, needs_llm, resp_q = raw
                meta = {}
            elif len(raw) == 2:
                message, resp_q = raw
                needs_llm = True
                meta = {}
            else:
                message, needs_llm, resp_q, meta = raw[0], True, None, {}
        else:
            message, needs_llm, resp_q, meta = raw, True, None, {}

        turn_id = (meta or {}).get("turn_id")
        Logger.set_turn_id(turn_id)
        gen_response.clear_interrupt()
        _turn_active.set()
        execution_start_time = time.perf_counter()
        response: Optional[str] = None
        turn_detail = f"speaker={UNKNOWN_USER_ID}, needs_llm={needs_llm}"

        try:
            if not isinstance(message, str):
                return_response = "Invalid queued message. Expected text."
                Logger.warn(return_response)
                _log_error(return_response)
                finish_turn(resp_q, return_response)
                continue

            username, text = split_message(message)
            turn_control.raise_if_interrupted()
            speaker = username or UNKNOWN_USER_ID
            msg_source = (meta or {}).get("source", "")
            is_monologue = msg_source == "monologue"
            store_input_memory = bool((meta or {}).get("store_input_memory", not is_monologue))
            store_output_memory = bool((meta or {}).get("store_output_memory", True))
            use_live_recall = bool((meta or {}).get("use_live_recall", not is_monologue))
            allow_finetune = bool((meta or {}).get("allow_finetune", not is_monologue))
            speak_input = bool((meta or {}).get("speak_input", not is_monologue))
            turn_start(msg_source, turn_id, speaker, text)
            turn_detail = f"speaker={speaker}, needs_llm={needs_llm}, input_chars={len(text)}"
            spam_check = False if is_monologue else is_spam_message(text)
            if spam_check:
                response = f"{text} was marked as spam."
                _log_suggestion(f"Spam message blocked: {text!r}")
                turn_finish(msg_source, turn_id, response)
                finish_turn(resp_q, response)
                continue

            if (meta or {}).get("memory_block"):
                memory_block = str((meta or {}).get("memory_block") or "").strip()
            elif (meta or {}).get("use_random_memory"):
                try:
                    memory_count = int((meta or {}).get("memory_count") or 3)
                except Exception:
                    memory_count = 3
                random_mems = await random_memories_main(n_results=max(1, memory_count))
                memory_block = format_memories(random_mems)
            elif use_live_recall:
                recalled = await recall_memories_total(text, user_id=speaker, n_results=3)
                memory_block = format_memories(recalled)
            else:
                memory_block = None
            turn_control.raise_if_interrupted()
            if store_input_memory:
                await store_user_memory_main(text, user_id=speaker, source=msg_source or "chat")
                Logger.print(f"Added: '{text}' to user memories!")
                _log_info(f"Stored user memory: speaker={speaker}, chars={len(text)}")

            current_chat_model = get_settings("chat_model") or ""
            llm_input = (meta or {}).get("llm_prompt") or message
            if memory_block:
                Logger.print(memory_block)
                if current_chat_model == "NovelAI" and not is_monologue:
                    _log_info(
                        "Don't feed memory to this model" \
                        "Known to have issues with it's own contamination."
                    )
                else:
                    llm_input = f"{memory_block}\n\nCurrent Message:\n{llm_input}"

            llm_input = prepend_contetx(llm_input)
            llm_start = time.perf_counter()
            try:
                response = await gen_response.send_user_text(llm_input)
                turn_control.raise_if_interrupted()
                Logger.quiet_print(response)
                llm_ms = (time.perf_counter() - llm_start) * 1000
                _log_perf("LLM response", llm_ms, detail=f"{turn_detail}, output_chars={len(response or '')}")
            except Exception as e:
                response = f"Error: {e}"
                Logger.error(f"LLM response failed: {e}")
                _log_error(f"LLM response failed: {e}", exc_info=True)

            if response and not response.startswith("Error:"):
                turn_control.raise_if_interrupted()
                filtered_words = check_for_bad_words(response, BAD_WORDS)
                if filtered_words:
                    Logger.warn(f"LLM Response Filtered. Detected moderation words: {filtered_words}")
                    _log_suggestion(f"LLM response filtered. speaker={speaker}, matched={filtered_words}")
                    response = CENSOR_PLACEHOLDER

            if allow_finetune and get_settings("enable_finetune_sampling"):
                if cleaned_chat_pairings(text, response):
                    await create_chat_pair(text, response)
                    Logger.print("Fine-tune sample saved.")
                    _log_info(f"Fine-tune sample saved: speaker={speaker}")

            if response and response != CENSOR_PLACEHOLDER and store_output_memory:
                try:
                    if is_monologue:
                        await store_ai_global_memory_main(
                            response,
                            source="monologue",
                            tags=["monologue"],
                        )
                        Logger.print(f"Added monologue output to AI global memory: '{response}'")
                        _log_info(f"Stored monologue AI memory: chars={len(response)}")
                    else:
                        await store_ai_memory_for_chatter_main(
                            response,
                            user_id=speaker,
                            source=msg_source or "chat",
                        )
                        Logger.print(f"Added: '{response}' to AI memory for {speaker}!")
                        _log_info(f"Stored AI memory: speaker={speaker}, chars={len(response)}")
                except Exception as e:
                    Logger.error(f"Error storing AI memory: {e}")
                    _log_error(f"Error storing AI memory: {e}", exc_info=True)
            _remember_completed_turn(
                source=msg_source,
                speaker=speaker,
                user_text=text,
                assistant_text=response or "",
            )
            emotion_task: Optional[asyncio.Task] = None
            if (response and response != CENSOR_PLACEHOLDER and not response.startswith("Error:")):
                emotion_task = asyncio.create_task(start_emotion_inf(response))
            is_user_source = msg_source in ("manual", "stt")
            caption_source = SOURCE_USER if is_user_source else SOURCE_NARRATOR
            input_caption = _incoming_caption_text(speaker, text, msg_source)
            if text and speak_input:
                caption_coordinator.set_pending(input_caption, source=caption_source)
            turn_control.raise_if_interrupted()
            narrator = get_narrator()
            if narrator.is_enabled() and text and speak_input:
                narrator_start = time.perf_counter()
                try:
                    await narrator.narrate_message(text, source=msg_source)
                    turn_control.raise_if_interrupted()
                except Exception as e:
                    Logger.warn(f"Narration Failed: {e}")
                    _log_error(f"Narration Failed: {e}", exc_info=True)
                _log_perf("Narrator playback", (time.perf_counter() - narrator_start) * 1000, detail=f"speaker={speaker}, chars={len(text)}")
            if text and speak_input:
                caption_coordinator.finish_pending()
            should_caption_reply = (
                response
                and response != CENSOR_PLACEHOLDER
                and not response.startswith("Error:")
            )
            if should_caption_reply:
                caption_coordinator.set_pending(response, source=SOURCE_LLM)

            tts_start = time.perf_counter()
            turn_control.raise_if_interrupted()
            playback = await gen_response.speak(response or "")
            await _wait_for_playback(playback)
            tts_ms = (time.perf_counter() - tts_start) * 1000
            _log_perf("TTS route/playback", tts_ms, detail=f"{turn_detail}, response_chars={len(response or '')}")
            if should_caption_reply:
                caption_coordinator.finish_pending()
            if emotion_task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(emotion_task), timeout=1.0)
                except (asyncio.TimeoutError, Exception):
                    pass

            try:
                if _vts_manager is not None:
                    reset_delay = float(get_settings("vts_emotion_reset_delay") or 0.35)
                    if reset_delay > 0:
                        await asyncio.sleep(reset_delay)
                    await _vts_manager.reset_current_emotion()
            except Exception as e:
                Logger.warn(f"Post-turn emotion reset failed: {e}")
                _log_error(f"Post-turn emotion reset failed: {e}", exc_info=True)

            execution_time = time.perf_counter() - execution_start_time
            total_execution_time = execution_time * 1000
            if execution_time > 600:
                formatted = time.strftime("%H:%M:%S", time.gmtime(execution_time))
                Logger.print(f"Total Generation Time: {formatted} ({total_execution_time:.0f} ms)")
            else:
                Logger.print(f"Total Generation Time: {execution_time:.2f} seconds ({total_execution_time:.0f} ms)")
            _log_perf("Total turn generation", total_execution_time,  detail=f"{turn_detail}, response_chars={len(response or '')}")
            await clear_caption()
            turn_finish(msg_source, turn_id, response)
            finish_turn(resp_q, response)

        except turn_control.TurnInterrupted:
            response = "Turn interrupted."
            Logger.warn(response)
            try:
                caption_coordinator.cancel_pending()
            except Exception:
                pass
            gen_response.stop_tts()
            turn_finish((meta or {}).get("source", "") if isinstance(meta, dict) else "", turn_id, response)
            finish_turn(resp_q, response)

        except Exception as e:
            response = response or f"Error: {e}"
            Logger.error(f"Error in final loop run: {e}")
            _log_error(f"Error in final loop run: {e}", exc_info=True)
            msg_source = (meta or {}).get("source", "") if isinstance(meta, dict) else ""
            turn_finish(msg_source, turn_id, response)
            finish_turn(resp_q, response)
        finally:
            _turn_active.clear()
            message_queue.task_done()

def thread_target():
    global async_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async_loop = loop
    loop.run_forever()
    async_loop = None

def start_loop():
    global async_loop
    t = threading.Thread(target=thread_target, daemon=True)
    t.start()
    while async_loop is None:
        time.sleep(0.01)
    asyncio.run_coroutine_threadsafe(main_loop(), async_loop)
