"""The one error type of the slot-fill engine."""


class SlotFillError(RuntimeError):
    """A step failed in a way the run can count (PromptForge refused, a job failed, a picture was unusable, a bank file is bad)."""
