"""High availability: scheduler leader election (enterprise)."""
from extensions import Registry


def register(registry: Registry) -> None:
    from nodeglow_ee.ha.coordinator import LeaderElectionCoordinator

    registry.set_scheduler_coordinator(LeaderElectionCoordinator())
    registry.enable_feature("ha_scheduler")
