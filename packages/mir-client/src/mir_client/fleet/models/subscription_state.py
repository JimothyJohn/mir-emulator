from enum import Enum


class SubscriptionState(str, Enum):
    SUBSCRIBED = "Subscribed"
    UNSUBSCRIBED = "Unsubscribed"

    def __str__(self) -> str:
        return str(self.value)
