"""Deduplicate bracket-group cancels; retain the manager's confirmation/requery gates."""
from functools import wraps


class GroupCancelAPI:
    def __init__(self, api, prefix):
        self.api, self.prefix = api, prefix
        self.groups, self.requested = {}, set()

    def __getattr__(self, name):
        return getattr(self.api, name)

    def call(self, method, path, *args, **kwargs):
        if method == "DELETE" and path.startswith("/v2/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            group = self.groups.get(order_id)
            if group is not None and group in self.requested:
                # A successful cancel request is NOT assumed to be a completed cancel.
                # Original manage() still polls open orders and refuses a competing sell.
                return None
            result = self.api.call(method, path, *args, **kwargs)
            if group is not None: self.requested.add(group)
            return result
        result = self.api.call(method, path, *args, **kwargs)
        if method == "GET" and path == "/v2/orders" and isinstance(result, list):
            for root in result:
                if root.get("side") != "buy" or not root.get("client_order_id", "").startswith(self.prefix): continue
                if not root.get("id") or not root.get("legs"): continue
                for order in [root]+root["legs"]:
                    if order.get("id"): self.groups[order["id"]] = root["id"]
        return result


def with_group_cancels(original, prefix):
    @wraps(original)
    def manage(api, clock, calendar):
        return original(GroupCancelAPI(api, prefix), clock, calendar)
    return manage
