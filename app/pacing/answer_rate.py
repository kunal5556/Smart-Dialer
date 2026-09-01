RECENT_WEIGHT = 0.7
BASELINE_WEIGHT = 0.3


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def observed_answer_rate(answered: int, total: int) -> float | None:
    if total <= 0:
        return None
    return answered / total


def blended_answer_rate(
    recent_answer_rate: float | None,
    baseline_answer_rate: float,
    min_answer_rate: float,
    max_answer_rate: float,
) -> float:
    if recent_answer_rate is None:
        blended = baseline_answer_rate
    else:
        blended = (
            RECENT_WEIGHT * recent_answer_rate + BASELINE_WEIGHT * baseline_answer_rate
        )
    return clamp(blended, min_answer_rate, max_answer_rate)


def is_volatile(
    recent_answer_rate: float | None,
    previous_answer_rate: float | None,
    threshold: float,
) -> bool:
    if recent_answer_rate is None or previous_answer_rate is None:
        return False
    return abs(recent_answer_rate - previous_answer_rate) > threshold
