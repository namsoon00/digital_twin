class NotificationDeliverySuppressed(RuntimeError):
    """A terminal policy decision, rather than a retryable transport failure."""
