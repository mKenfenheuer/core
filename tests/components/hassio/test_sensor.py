"""The tests for the hassio sensors."""

from dataclasses import replace
from datetime import timedelta
import os
from unittest.mock import AsyncMock, Mock, patch

from aiohasupervisor import SupervisorError, SupervisorNotFoundError
from aiohasupervisor.models import GPU, AddonState, HardwareGPU, InstalledAddonComplete
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from homeassistant import config_entries
from homeassistant.components.hassio import DOMAIN
from homeassistant.components.hassio.const import (
    HASSIO_STATS_UPDATE_INTERVAL,
    REQUEST_REFRESH_DELAY,
)
from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorStateClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .common import MOCK_REPOSITORIES, MOCK_STORE_ADDONS

MOCK_ENVIRON = {"SUPERVISOR": "127.0.0.1", "SUPERVISOR_TOKEN": "abcdefgh"}


@pytest.fixture(autouse=True)
def mock_all(
    addon_installed: AsyncMock,
    store_info: AsyncMock,
    addon_stats: AsyncMock,
    addon_changelog: AsyncMock,
    resolution_info: AsyncMock,
    jobs_info: AsyncMock,
    host_info: AsyncMock,
    supervisor_root_info: AsyncMock,
    homeassistant_info: AsyncMock,
    supervisor_info: AsyncMock,
    addons_list: AsyncMock,
    network_info: AsyncMock,
    os_info: AsyncMock,
    homeassistant_stats: AsyncMock,
    supervisor_stats: AsyncMock,
    ingress_panels: AsyncMock,
) -> None:
    """Mock all setup requests."""
    host_info.return_value = replace(host_info.return_value, agent_version="1.0.0")
    addons_list.return_value[1] = replace(
        addons_list.return_value[1], version_latest="3.2.0", update_available=True
    )

    def mock_addon_info(slug: str):
        addon = Mock(
            spec=InstalledAddonComplete,
            to_dict=addon_installed.return_value.to_dict,
            **addon_installed.return_value.to_dict(),
        )
        if slug == "test":
            addon.name = "test"
            addon.slug = "test"
            addon.version = "2.0.0"
            addon.version_latest = "2.0.1"
            addon.update_available = True
            addon.state = AddonState.STARTED
            addon.url = "https://github.com/home-assistant/addons/test"
            addon.auto_update = True
        else:
            addon.name = "test2"
            addon.slug = "test2"
            addon.version = "3.1.0"
            addon.version_latest = "3.2.0"
            addon.update_available = True
            addon.state = AddonState.STOPPED
            addon.url = "https://github.com"
            addon.auto_update = False

        return addon

    addon_installed.side_effect = mock_addon_info


@pytest.mark.parametrize(
    ("store_addons", "store_repositories"), [(MOCK_STORE_ADDONS, MOCK_REPOSITORIES)]
)
@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        ("sensor.home_assistant_operating_system_version", "1.0.0"),
        ("sensor.home_assistant_operating_system_newest_version", "1.0.0"),
        ("sensor.home_assistant_host_os_agent_version", "1.0.0"),
        ("sensor.home_assistant_core_cpu_percent", "0.99"),
        ("sensor.home_assistant_supervisor_cpu_percent", "0.99"),
        ("sensor.test_version", "2.0.0"),
        ("sensor.test_newest_version", "2.0.1"),
        ("sensor.test2_version", "3.1.0"),
        ("sensor.test2_newest_version", "3.2.0"),
        ("sensor.test_cpu_percent", "0.99"),
        ("sensor.test2_cpu_percent", "unavailable"),
        ("sensor.test_memory_percent", "4.59"),
        ("sensor.test2_memory_percent", "unavailable"),
    ],
)
async def test_sensor(
    hass: HomeAssistant,
    entity_id,
    expected,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test hassio OS and addons sensor."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    config_entry.add_to_hass(hass)

    with patch.dict(os.environ, MOCK_ENVIRON):
        result = await async_setup_component(
            hass,
            DOMAIN,
            {"hassio": {}},
        )
        assert result
    await hass.async_block_till_done()

    # Verify that the entity is disabled by default.
    assert hass.states.get(entity_id) is None

    # Enable the entity.
    entity_registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    # There is a REQUEST_REFRESH_DELAYs cooldown on the debouncer
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=REQUEST_REFRESH_DELAY)
    )
    await hass.async_block_till_done()

    # Verify that the entity have the expected state.
    state = hass.states.get(entity_id)
    assert state.state == expected


@pytest.mark.parametrize(
    ("store_addons", "store_repositories"), [(MOCK_STORE_ADDONS, MOCK_REPOSITORIES)]
)
@pytest.mark.parametrize(
    ("disk_life_time", "expected"),
    [
        pytest.param(12.5, "12.5", id="reported"),
        # Supervisor only reports a lifetime for NVMe and eMMC disks.
        pytest.param(None, STATE_UNKNOWN, id="not_reported"),
    ],
)
@patch.dict(os.environ, MOCK_ENVIRON)
async def test_disk_lifetime_sensor(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    host_info: AsyncMock,
    disk_life_time: float | None,
    expected: str,
) -> None:
    """Test the disk lifetime sensor."""
    entity_id = "sensor.home_assistant_host_disk_lifetime"
    host_info.return_value = replace(
        host_info.return_value, disk_life_time=disk_life_time
    )
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {"hassio": {}})
    await hass.async_block_till_done()

    entry = entity_registry.async_get(entity_id)
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    entity_registry.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == expected
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE
    assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT


@pytest.mark.parametrize(
    ("store_addons", "store_repositories"), [(MOCK_STORE_ADDONS, MOCK_REPOSITORIES)]
)
@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        ("sensor.test_cpu_percent", "0.99"),
        ("sensor.test_memory_percent", "4.59"),
    ],
)
@patch.dict(os.environ, MOCK_ENVIRON)
async def test_stats_addon_sensor(
    hass: HomeAssistant,
    entity_id,
    expected,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
    freezer: FrozenDateTimeFactory,
    addon_stats: AsyncMock,
) -> None:
    """Test stats addons sensor."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    config_entry.add_to_hass(hass)

    assert await async_setup_component(
        hass,
        DOMAIN,
        {"hassio": {}},
    )
    await hass.async_block_till_done()

    # Verify that the entity is disabled by default.
    assert hass.states.get(entity_id) is None

    addon_stats.side_effect = SupervisorError
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert "Could not fetch stats" not in caplog.text

    addon_stats.side_effect = None
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert "Could not fetch stats" not in caplog.text

    # Enable the entity and wait for the reload to complete.
    entity_registry.async_update_entity(entity_id, disabled_by=None)
    freezer.tick(config_entries.RELOAD_AFTER_UPDATE_DELAY)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.LOADED
    # Verify the entity is still enabled
    assert entity_registry.async_get(entity_id).disabled_by is None

    # The config entry just reloaded, so we need to wait for the next update
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(entity_id) is not None

    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    # Verify that the entity have the expected state.
    state = hass.states.get(entity_id)
    assert state.state == expected

    addon_stats.side_effect = SupervisorError
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(entity_id)
    assert state.state == STATE_UNAVAILABLE
    assert "Could not fetch stats" in caplog.text

    # Disable the entity again and verify stats API calls stop
    addon_stats.side_effect = None
    addon_stats.reset_mock()
    entity_registry.async_update_entity(
        entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    freezer.tick(config_entries.RELOAD_AFTER_UPDATE_DELAY)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.LOADED

    # After reload with entity disabled, stats should not be fetched
    addon_stats.reset_mock()
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    addon_stats.assert_not_called()


@pytest.mark.parametrize(
    ("store_addons", "store_repositories"), [(MOCK_STORE_ADDONS, MOCK_REPOSITORIES)]
)
@patch.dict(os.environ, MOCK_ENVIRON)
async def test_stats_addon_sensor_rename(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
    addon_stats: AsyncMock,
) -> None:
    """Test a renamed stats sensor keeps its stats subscription."""
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {"hassio": {}})
    await hass.async_block_till_done()
    entity_registry.async_update_entity("sensor.test_cpu_percent", disabled_by=None)
    freezer.tick(config_entries.RELOAD_AFTER_UPDATE_DELAY)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.test_cpu_percent") is not None

    entity_registry.async_update_entity(
        "sensor.test_cpu_percent", new_entity_id="sensor.renamed_cpu_percent"
    )
    await hass.async_block_till_done()

    addon_stats.reset_mock()
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    addon_stats.assert_called_with("test")
    assert hass.states.get("sensor.renamed_cpu_percent").state == "0.99"
    assert hass.states.get("sensor.test_cpu_percent") is None

    # Disabling the renamed entity unsubscribes it, so stats fetching stops
    entity_registry.async_update_entity(
        "sensor.renamed_cpu_percent", disabled_by=er.RegistryEntryDisabler.USER
    )
    freezer.tick(config_entries.RELOAD_AFTER_UPDATE_DELAY)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.LOADED

    addon_stats.reset_mock()
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    addon_stats.assert_not_called()


MOCK_GPUS = HardwareGPU(
    gpus=[
        GPU(
            address="0000:01:00.0",
            vendor="nvidia",
            name="NVIDIA GeForce RTX 3090",
            driver="nvidia",
            driver_version="615.78.08",
            utilization=7.0,
            memory_used=2982150144,
            memory_total=25769803776,
            temperature=34.0,
            power=16.37,
        ),
        GPU(
            address="0000:00:02.0",
            vendor="intel",
            name="Intel GPU [8086:46a6]",
            driver="i915",
            driver_version="6.18.60-haos",
            utilization=None,
            memory_used=None,
            memory_total=None,
            temperature=None,
            power=None,
        ),
    ]
)
GPU_ENTITY_PREFIX = "sensor.nvidia_geforce_rtx_3090"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_gpu_sensors(
    hass: HomeAssistant,
    supervisor_client: AsyncMock,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test sensors for the statistics of the host GPUs."""
    supervisor_client.hardware.gpu.return_value = MOCK_GPUS
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    config_entry.add_to_hass(hass)
    with patch.dict(os.environ, MOCK_ENVIRON):
        assert await async_setup_component(hass, DOMAIN, {"hassio": {}})
    await hass.async_block_till_done()

    assert hass.states.get(f"{GPU_ENTITY_PREFIX}_utilization").state == "7.0"
    assert float(
        hass.states.get(f"{GPU_ENTITY_PREFIX}_memory_used").state
    ) == pytest.approx(2982150144 / 1024**3)
    assert hass.states.get(f"{GPU_ENTITY_PREFIX}_memory_percent").state == "11.6"
    assert hass.states.get(f"{GPU_ENTITY_PREFIX}_temperature").state == "34.0"
    assert hass.states.get(f"{GPU_ENTITY_PREFIX}_power").state == "16.37"

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "gpu_0000:01:00.0"), config_entry.entry_id
    )
    assert device.name == "NVIDIA GeForce RTX 3090"
    assert device.manufacturer == "NVIDIA"
    assert device.sw_version == "615.78.08"
    assert (
        device.via_device_id
        == device_registry.async_get_device_by_identifier(
            (DOMAIN, "host"), config_entry.entry_id
        ).id
    )

    # The driver of the Intel GPU reports no statistics
    assert not [
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        )
        if entry.unique_id.startswith("gpu_0000:00:02.0")
    ]

    supervisor_client.hardware.gpu.return_value = HardwareGPU(gpus=[])
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(f"{GPU_ENTITY_PREFIX}_utilization").state == (
        STATE_UNAVAILABLE
    )


@pytest.mark.parametrize(
    ("side_effect", "log_message"),
    [
        pytest.param(SupervisorNotFoundError(), "", id="supervisor-without-support"),
        pytest.param(
            SupervisorError("nvidia-smi failed"),
            "Could not fetch GPU statistics: nvidia-smi failed",
            id="supervisor-error",
        ),
    ],
)
async def test_gpu_sensors_no_data(
    hass: HomeAssistant,
    supervisor_client: AsyncMock,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
    side_effect: Exception,
    log_message: str,
) -> None:
    """Test no GPU sensors without GPU statistics from the Supervisor."""
    supervisor_client.hardware.gpu.side_effect = side_effect
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    config_entry.add_to_hass(hass)
    with patch.dict(os.environ, MOCK_ENVIRON):
        assert await async_setup_component(hass, DOMAIN, {"hassio": {}})
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert not [
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        )
        if entry.unique_id.startswith("gpu_")
    ]
    assert log_message in caplog.text


@pytest.mark.parametrize(
    ("side_effect", "return_value"),
    [
        pytest.param(None, HardwareGPU(gpus=[]), id="no-gpus"),
        pytest.param(SupervisorError("nvidia-smi failed"), None, id="supervisor-error"),
    ],
)
@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_gpu_sensors_added_later(
    hass: HomeAssistant,
    supervisor_client: AsyncMock,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    side_effect: Exception | None,
    return_value: HardwareGPU | None,
) -> None:
    """Test sensors for GPUs that appear after setting up."""
    supervisor_client.hardware.gpu.side_effect = side_effect
    supervisor_client.hardware.gpu.return_value = return_value
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    config_entry.add_to_hass(hass)
    with patch.dict(os.environ, MOCK_ENVIRON):
        assert await async_setup_component(hass, DOMAIN, {"hassio": {}})
    await hass.async_block_till_done()

    def gpu_unique_ids() -> list[str]:
        return [
            entry.unique_id
            for entry in er.async_entries_for_config_entry(
                entity_registry, config_entry.entry_id
            )
            if entry.unique_id.startswith("gpu_")
        ]

    assert not gpu_unique_ids()

    supervisor_client.hardware.gpu.side_effect = None
    supervisor_client.hardware.gpu.return_value = MOCK_GPUS
    freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(f"{GPU_ENTITY_PREFIX}_utilization").state == "7.0"
    assert sorted(gpu_unique_ids()) == [
        "gpu_0000:01:00.0_memory_percent",
        "gpu_0000:01:00.0_memory_used",
        "gpu_0000:01:00.0_power",
        "gpu_0000:01:00.0_temperature",
        "gpu_0000:01:00.0_utilization",
    ]

    # A GPU that disappears and reappears keeps its entities without duplicates
    for result in (HardwareGPU(gpus=[]), MOCK_GPUS, MOCK_GPUS):
        supervisor_client.hardware.gpu.return_value = result
        freezer.tick(HASSIO_STATS_UPDATE_INTERVAL + timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(f"{GPU_ENTITY_PREFIX}_utilization").state == "7.0"
    assert len(gpu_unique_ids()) == 5
    assert "already exists" not in caplog.text
