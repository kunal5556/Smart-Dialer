class StateMachineError(Exception):
    pass


class InvalidStateTransition(StateMachineError):
    def __init__(self, current: str, target: str, actor: str) -> None:
        self.current = current
        self.target = target
        self.actor = actor
        super().__init__(f"Transition {current} -> {target} is not valid (attempted by {actor})")


class UnauthorizedTransitionActor(StateMachineError):
    def __init__(self, current: str, target: str, actor: str) -> None:
        self.current = current
        self.target = target
        self.actor = actor
        super().__init__(f"Actor {actor} may not trigger transition {current} -> {target}")
