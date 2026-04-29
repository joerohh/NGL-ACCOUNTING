"""LIVE TMS API field-name probe.

Skipped by default. Run manually with:
  cd agent && TMS_API_LIVE=1 python -m pytest tests/test_tms_data/test_field_names_live.py -v -s

Requires a working TMS API token in .env and a valid WO# in TEST_WO_NO env var.
"""

import os
import pytest


@pytest.mark.skipif(
    not os.getenv("TMS_API_LIVE"),
    reason="Set TMS_API_LIVE=1 to run live TMS API probes",
)
@pytest.mark.asyncio
async def test_print_wo_field_names():
    from services.tms_api import TMSApiClient

    api = TMSApiClient()
    assert api.is_configured(), "TMS API not configured (.env missing TMS_API_TOKEN)"

    wo_no = os.getenv("TEST_WO_NO", "LM2602170009")  # known-good per memory
    wo = await api.get_work_order(wo_no)
    assert wo, f"No WO returned for {wo_no}"

    print("\n=== TMS WO keys for", wo_no, "===")
    for k in sorted(wo.keys()):
        v = wo[k]
        sample = (str(v)[:60] + "...") if isinstance(v, str) and len(str(v)) > 60 else v
        print(f"  {k!r}: {sample!r}")

    # Assert at least one of each expected field-name family is present.
    chassis_keys = {"chassis_no", "chassis", "chassis_number"}
    cnee_keys = {"billto", "bill_to", "consignee", "cnee"}
    assert chassis_keys & set(wo.keys()) or cnee_keys & set(wo.keys()), (
        "Neither chassis nor CNEE candidate keys present — extractor list needs update"
    )
