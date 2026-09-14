"""Apply collection configuration changes at cycle boundaries, without restarts."""


class ReloadingCollectionRunner:
    def __init__(self, runner, factory, settings_reader, settings):
        self.runner = runner
        self.factory = factory
        self.settings_reader = settings_reader
        self.fingerprint = self.collection_settings(settings)

    @staticmethod
    def collection_settings(settings):
        prefixes = ('external', 'news', 'informationFollowup', 'investmentCalendar', 'opendart', 'alphaVantage', 'fred', 'coinGecko', 'kis')
        return {key: value for key, value in settings.items() if key.startswith(prefixes)}

    def run_once(self, *args, **kwargs):
        settings = self.settings_reader()
        fingerprint = self.collection_settings(settings)
        if fingerprint != self.fingerprint:
            self.runner = self.factory(settings)
            self.fingerprint = fingerprint
        return self.runner.run_once(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.runner, name)
