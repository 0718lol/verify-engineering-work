"""Tenant-aware feature flag lookup."""


class FeatureFlags:
    def __init__(self, provider):
        self._provider = provider
        self._cache = {}

    def enabled(self, tenant_id, flag_name):
        if flag_name not in self._cache:
            self._cache[flag_name] = self._provider(tenant_id, flag_name)
        return self._cache[flag_name]
