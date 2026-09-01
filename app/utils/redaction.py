VISIBLE_DIGITS = 4
REDACTED_PLACEHOLDER = "****"


def redact_phone(phone_number: str | None) -> str:
    if not phone_number:
        return REDACTED_PLACEHOLDER
    digits = "".join(character for character in phone_number if character.isdigit())
    if len(digits) <= VISIBLE_DIGITS:
        return REDACTED_PLACEHOLDER
    return f"{REDACTED_PLACEHOLDER}{digits[-VISIBLE_DIGITS:]}"
