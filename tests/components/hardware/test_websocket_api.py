"""Test the hardware websocket API."""

from collections import namedtuple
import datetime as dt
from unittest.mock import AsyncMock, patch

from aiohasupervisor import SupervisorError
from aiohasupervisor.models import GPU, HardwareGPU
from freezegun.api import FrozenDateTimeFactory
import psutil_home_assistant as ha_psutil
import pytest
from pytest_homeassistant.typing import WebSocketGenerator

from homeassistant.components.hardware.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util


async def test_board_info(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Test we can get the board info."""
    assert await async_setup_component(hass, DOMAIN, {})

    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "hardware/info"})
    msg = await client.receive_json()

    assert msg["id"] == 1
    assert msg["success"]
    assert msg["result"] == {"hardware": []}


TEST_TIME_ADVANCE_INTERVAL = dt.timedelta(seconds=5 + 1)


async def test_system_status_subscription(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test websocket system status subscription."""

    mock_psutil = None
    orig_psutil_wrapper = ha_psutil.PsutilWrapper

    def create_mock_psutil():
        nonlocal mock_psutil
        mock_psutil = orig_psutil_wrapper()
        return mock_psutil

    with patch(
        "homeassistant.components.hardware.ha_psutil.PsutilWrapper",
        wraps=create_mock_psutil,
    ):
        assert await async_setup_component(hass, DOMAIN, {})

    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "hardware/subscribe_system_status"})
    response = await client.receive_json()
    assert response["success"]

    VirtualMem = namedtuple("VirtualMemory", ["available", "percent", "total"])  # noqa: PYI024
    vmem = VirtualMem(10 * 1024**2, 50, 30 * 1024**2)

    with (
        patch.object(
            mock_psutil.psutil,
            "cpu_percent",
            return_value=123,
        ),
        patch.object(
            mock_psutil.psutil,
            "virtual_memory",
            return_value=vmem,
        ),
    ):
        freezer.tick(TEST_TIME_ADVANCE_INTERVAL)
        await hass.async_block_till_done()
        await hass.async_block_till_done()

    response = await client.receive_json()
    assert response["event"] == {
        "cpu_percent": 123,
        "memory_free_mb": 10.0,
        "memory_used_mb": 20.0,
        "memory_used_percent": 50,
        "gpus": [],
        "timestamp": dt_util.utcnow().isoformat(),
    }

    # Unsubscribe
    await client.send_json({"id": 8, "type": "unsubscribe_events", "subscription": 1})
    response = await client.receive_json()
    assert response["success"]

    with (
        patch.object(mock_psutil.psutil, "cpu_percent") as cpu_mock,
        patch.object(mock_psutil.psutil, "virtual_memory") as vmem_mock,
    ):
        freezer.tick(TEST_TIME_ADVANCE_INTERVAL)
        await hass.async_block_till_done()
        cpu_mock.assert_not_called()
        vmem_mock.assert_not_called()


GPU_STATUS = {
    "address": "0000:01:00.0",
    "name": "NVIDIA GeForce RTX 3090",
    "vendor": "nvidia",
    "driver": "nvidia",
    "driver_version": "615.78.08",
    "utilization_percent": 7.0,
    "memory_used_mb": 2844.0,
    "memory_total_mb": 24576.0,
    "temperature": 34.0,
    "power": 16.37,
}


@pytest.mark.parametrize(
    ("gpu_result", "expected"),
    [
        pytest.param(
            HardwareGPU(
                gpus=[
                    GPU(
                        address="0000:01:00.0",
                        vendor="nvidia",
                        name="NVIDIA GeForce RTX 3090",
                        driver="nvidia",
                        driver_version="615.78.08",
                        utilization=7.0,
                        memory_used=2844 * 1024**2,
                        memory_total=24576 * 1024**2,
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
            ),
            [
                GPU_STATUS,
                {
                    "address": "0000:00:02.0",
                    "name": "Intel GPU [8086:46a6]",
                    "vendor": "intel",
                    "driver": "i915",
                    "driver_version": "6.18.60-haos",
                    "utilization_percent": None,
                    "memory_used_mb": None,
                    "memory_total_mb": None,
                    "temperature": None,
                    "power": None,
                },
            ],
            id="gpus",
        ),
        pytest.param(SupervisorError(), [], id="supervisor-error"),
    ],
)
async def test_system_status_subscription_gpus(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    freezer: FrozenDateTimeFactory,
    supervisor_client: AsyncMock,
    gpu_result: HardwareGPU | Exception,
    expected: list[dict[str, str | float | None]],
) -> None:
    """Test system status includes GPUs reported by the Supervisor."""
    supervisor_client.hardware.gpu.side_effect = [gpu_result]
    hass.config.components.add("hassio")

    with (
        patch(
            "homeassistant.components.hardware.websocket_api.is_hassio",
            return_value=True,
        ),
        patch(
            "homeassistant.components.hardware.websocket_api.get_supervisor_client",
            return_value=supervisor_client,
        ),
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        client = await hass_ws_client(hass)

        await client.send_json({"id": 1, "type": "hardware/subscribe_system_status"})
        response = await client.receive_json()
        assert response["success"]

        freezer.tick(TEST_TIME_ADVANCE_INTERVAL)
        await hass.async_block_till_done()

        response = await client.receive_json()

    assert response["event"]["gpus"] == expected
