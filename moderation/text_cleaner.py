import re

NOVELAI_SPECIAL_SYMBOLS_RE = re.compile(
    r"\*\*\*|⁂|\{ |\ }|\[ |\]|----|======|─|##"
)


def remove_novelai_special_symbols(text: str) -> str:
    return NOVELAI_SPECIAL_SYMBOLS_RE.sub("", text or "").strip()


def sanitize_text(text: str) -> str:
    text = re.sub(r'^[,;:\-\.]+', '', text).strip()
    text = re.sub(r'[,;:\-\.]+$', '', text).strip()
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'\[\s*.*?\s*\]', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s([,.!?])', r'\1', text)
    return text.strip()


def split_into_sentences(text):
    text = text.replace("\n", " ")
    text = text.replace("...", "<ELLIPSIS>")
    sentence_endings = re.compile(r'([.!?])')
    parts = sentence_endings.split(text)
    sentences = [''.join(pair).strip() for pair in zip(parts[0::2], parts[1::2])]
    if len(parts) % 2 != 0:
        last_part = parts[-1].strip()
        if last_part:
            sentences.append(last_part)
    sentences = [sentence.replace("<ELLIPSIS>", "...") for sentence in sentences]
    sentences = [sentence for sentence in sentences if sentence]
    return sentences


def separate_sentences(text):
    sentences = split_into_sentences(text)
    return ' '.join(sentence.strip() for sentence in sentences if sentence.strip())


def process_final_text(text: str) -> str:
    text = separate_sentences(text)
    return sanitize_text(text)


def split_message(text):
    text = text.strip()
    if ':' in text:
        username, message = text.split(':', 1)
        message = message.strip()
    else:
        username, message = "unknown", text
    return username.strip(), message.strip()


def normalize_chat_string(text: str) -> str:
    text = (text or "").strip()
    return text


def cleaned_chat_pairings(user_text: str, assistant_text: str) -> bool:
    if len(user_text.strip()) < 3:
        return False
    if len(assistant_text.strip()) < 10:
        return False
    if assistant_text.startswith("Error:"):
        return False
    return True


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text
