"""Construct module dependencies after the runner has opened its state."""

from typing import Callable, Protocol
from inspect import signature

from ..http import RequestScheduler
from ..pipeline_contracts import CheckpointStore
from .models import ModuleSet, RunOptions


class ModuleFactory(Protocol):
    def build(self, options: RunOptions, checkpoints: CheckpointStore) -> ModuleSet: ...


class StaticModuleFactory:
    """Compatibility adapter for callers that already supply a ModuleSet."""

    def __init__(self, modules: ModuleSet):
        self.modules = modules

    def build(self, options: RunOptions, checkpoints: CheckpointStore) -> ModuleSet:
        return self.modules


def checkpoint_kwargs(component: Callable[..., object], checkpoints: CheckpointStore) -> dict[str, CheckpointStore]:
    """Opt into the agreed keyword when a member's constructor declares it.

    Absence means baseline support only; constructor failures always propagate.
    This lets separately developed module upgrades be integrated without
    catch-and-retry construction that could conceal programming errors.
    """
    return {"checkpoints": checkpoints} if "checkpoints" in signature(component).parameters else {}


class LiveModuleFactory:
    def __init__(self, *, scheduler: RequestScheduler | None = None) -> None:
        self.scheduler = scheduler

    def build(self, options: RunOptions, checkpoints: CheckpointStore) -> ModuleSet:
        from ..http import RequestScheduler
        from ..discovery.api import ApiClient
        from ..discovery.service import DiscoveryModule
        from ..metadata.api import OaiClient
        from ..metadata.service import MetadataModule
        from ..references.api import ArxivDateClient, SemanticScholarClient
        from ..references.service import ReferencesModule
        from ..sources.api import ArxivSourceClient
        from ..sources.extractor import SourceExtractor
        from ..sources.service import SourcesModule

        scheduler = self.scheduler
        if scheduler is None:
            scheduler = RequestScheduler(**checkpoint_kwargs(RequestScheduler, checkpoints))
        return ModuleSet(
            DiscoveryModule(ApiClient(scheduler=scheduler), max_candidates=options.max_candidates,
                            checkpoints=checkpoints),
            MetadataModule(OaiClient(scheduler=scheduler)),
            SourcesModule(ArxivSourceClient(scheduler=scheduler), SourceExtractor()),
            ReferencesModule(SemanticScholarClient(scheduler=scheduler),
                             ArxivDateClient(scheduler=scheduler),
                             **checkpoint_kwargs(ReferencesModule, checkpoints)),
        )


class MockModuleFactory:
    def build(self, options: RunOptions, checkpoints: CheckpointStore) -> ModuleSet:
        from .mock import make_mock_modules
        return make_mock_modules(options, checkpoints=checkpoints)
