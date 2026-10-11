# VTube Studio: Simple and Advanced modes

Restart AI Companion, open its VTube Studio page, and use **Control mode** above Action Mapping.

**Simple** is the default: one talking hotkey per emotion with the existing turn reset behavior. Existing mappings remain available. **Advanced** provides ordered actions for each emotion's talking, idle, and reset phases, plus a Full Reset sequence. The first switch to Advanced copies existing sequences; subsequent switches retain each mode's saved mappings separately.

## First live test

1. Enable and connect VTube Studio, with the intended model loaded. Refresh hotkeys.
2. Switch to **Advanced**, then click **Refresh expressions**.
3. Choose an emotion such as `happy`, with phase `talking`.
4. Add **Expression On** using one of your model's expression files. Then add a **Hotkey** that plays a gesture or animation. Order the expression first to let it remain active while the animation plays.
5. If that hotkey leaves an effect active, add its corresponding reset hotkey to the emotion's `reset` phase.
6. Use **Preview sequence**. It runs the selected draft, waits one second, runs that emotion's reset actions, and cleans up expressions it activated. Previewing a reset sequence runs it once. Save with **Save sequences** when satisfied.
7. Set **After speech** to `idle` to retain the emotion after speech and run its idle actions, or `reset` to clear it after speech. Save again. For the first conversation test, leave idle actions empty so the talking expression simply persists.
8. Ensure emotion inference is enabled, then run a conversation. Check the log for `VTS advanced talking` and the inferred emotion. Confirm expression and gesture overlap, then check the selected after-speech behavior.
9. Test another turn with the same emotion, another emotion, Full Reset, switching back to Simple, and restarting the app to confirm saved configuration loads.

## Action behavior

- **Expression On / Off:** explicit state changes using `.exp3.json` files. Repeated On actions do not toggle an expression off. Existing expression-toggle hotkeys with known expression files also use explicit state changes.
- **Hotkey:** a model hotkey selected from the cache. Animations, item toggles, and other persistent effects need suitable reset actions if they do not stop themselves.
- **Delay:** pauses between actions, from 0 to 5 seconds each. Up to 32 actions and 15 total seconds of delay per sequence are supported.
- **Talking:** runs when an emotion is inferred. A new speech cycle first resets the previous cycle, including when the emotion repeats.
- **Idle:** runs after speech when selected under After speech. Add explicit Off actions here if you want to remove talking expressions while entering idle.
- **Reset:** runs before the next emotion/speech cycle, on Full Reset, and when switching modes or closing the connection. Expressions activated by the advanced engine are also cleaned up automatically. Expressions already active before the engine used them are preserved unless your sequence explicitly turns them off.

Actions are sent in order. An expression and an animation can overlap, but the requests are not an atomic simultaneous operation. VTube Studio and the model determine whether their parameters conflict. The fade setting controls expression activation; VTube Studio uses the activation fade when later deactivating that expression.

Emotion inference runs alongside speech. The existing post-speech timeout cancels unfinished inference/actions after one second, so long talking delays can be interrupted when the spoken response is short. Start with no delays for live testing. Interrupted actions attempt reset and expression cleanup; a lost VTS connection can prevent cleanup until reconnection.

## Saved configuration and implementation

Configuration is in `files/userdata/vts_emotion_map.json`: `control_mode` selects `simple` or `advanced`; `advanced.emotions` contains `talking`, `idle`, `reset`, and `after_speech`, and `advanced.full_reset` contains global reset actions. Editing advanced sequences does not replace the Simple mappings.

The design follows the talking/idle/reset sequence approach in the [w-AI-fu fork](https://github.com/TheRManVT/w-AI-fu_v2.R), with explicit expression control through the [VTube Studio API](https://github.com/DenchiSoft/VTubeStudio#expression-activation-and-deactivation).

Backend regression tests use a simulated VTS connection; live visual behavior still needs checking with your model.

## Isolated LLM emotion classification

**Classification provider** defaults to **Same as conversation**. Select OpenAI, Claude, Gemini, Grok, NovelAI, or Local to use a classifier independently of the chat provider, then click **Save classification settings**. Each provider uses its credentials and configuration from LLM settings. The model field follows the selected provider and preserves separate overrides. A missing credential or failed request produces the existing logged heuristic fallback; it never silently switches to another provider. Existing installations keep following the chat provider until a selection is saved.

In Emotion Inference, **LLM** now sends a separate request containing only the allowed emotion labels, character name, and latest reply. A dedicated system instruction requests exactly one label. It does not read or write the conversational history, include the character's personality prompt, or attach vision context. Heuristic mode remains keyword-based; in LLM mode it is used only if classification fails, times out, or returns an invalid label.

**Classification model** is saved separately for each provider. Leave it blank to use the provider's current model. For OpenAI fine-tune IDs beginning with `ft:`, Automatic uses the base model encoded in that ID, matching w-AI-fu's separation from the fine-tune. An explicit model overrides Automatic. Local classification uses the already-loaded model with empty history and a separate system instruction; it does not load a second model.

The classification generation budget defaults to **4096**, including thinking where applicable. It is independent of the conversational response limit; legacy NovelAI requests are capped at 512. The existing six-second classification timeout and post-speech cancellation still apply, so a large token allowance does not extend how long the app waits.

After restarting, select LLM and test a reply containing “haha” or “good.” Expect `VTS classification: provider=..., model=..., isolated request` followed by `Inferred emotion: ...`, or an explicit failure/fallback message. The classification no longer produces the conversational `prompt_msgs` log entry. Only configured emotion labels are offered, so add mappings for confused/angry/etc. if you want those choices available.
