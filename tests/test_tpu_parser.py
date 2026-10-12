"""Fixture-based tests for parse_techpowerup_detail (GPU path).

The HTML fixtures are synthetic but mirror the selectors the parser reads
(`h1.gpuname` + `.gpuspecs dl` with dt/dd), so they exercise the real
extraction logic: memory size/type (incl. GDDR7), clocks, TDP, and bus interface.
"""

from app.scrapers.lookup import parse_techpowerup_detail


def _tpu_gpu_html(name, rows):
    dls = "".join(f"<dl><dt>{dt}</dt><dd>{dd}</dd></dl>" for dt, dd in rows)
    return f'<html><body><h1 class="gpuname">{name}</h1><div class="gpuspecs">{dls}</div></body></html>'


class TestParseTpuGpu:
    def test_rtx_5090_gddr7_and_all_fields(self):
        html = _tpu_gpu_html(
            "NVIDIA GeForce RTX 5090",
            [
                ("Memory Size", "32 GB"),
                ("Memory Type", "GDDR7"),
                ("Base Clock", "2017 MHz"),
                ("Boost Clock", "2407 MHz"),
                ("TDP", "575 W"),
                ("Bus Interface", "PCIe 5.0 x16"),
            ],
        )
        specs = parse_techpowerup_detail(html, "GPU", "https://tpu/x")

        assert specs["manufacturer"] == "NVIDIA"
        assert "RTX 5090" in specs["model"]
        assert specs["gpu_memory_size"] == 32 * 1024   # stored in MB
        assert specs["gpu_memory_type"] == "GDDR7"      # previously unrecognized
        assert specs["gpu_base_clock"] == 2017          # previously not extracted
        assert specs["gpu_boost_clock"] == 2407         # previously not extracted
        assert specs["gpu_tdp"] == 575
        assert specs["gpu_bus_interface"] == "PCIe 5.0 x16"  # previously not extracted

    def test_amd_gddr6_still_parses(self):
        html = _tpu_gpu_html(
            "AMD Radeon RX 7900 XTX",
            [
                ("Memory Size", "24 GB"),
                ("Memory Type", "GDDR6"),
                ("GPU Clock", "1929 MHz"),   # AMD labels base clock "GPU Clock"
                ("Boost Clock", "2498 MHz"),
                ("Bus Interface", "PCIe 4.0 x16"),
            ],
        )
        specs = parse_techpowerup_detail(html, "GPU", "https://tpu/y")

        assert specs["manufacturer"] == "AMD"
        assert specs["gpu_memory_type"] == "GDDR6"
        assert specs["gpu_base_clock"] == 1929
        assert specs["gpu_boost_clock"] == 2498
        assert specs["gpu_bus_interface"] == "PCIe 4.0 x16"

    def test_clock_with_thousands_separator(self):
        html = _tpu_gpu_html("NVIDIA GeForce RTX 3060", [("Base Clock", "1,320 MHz")])
        specs = parse_techpowerup_detail(html, "GPU", "https://tpu/z")
        assert specs["gpu_base_clock"] == 1320

    def test_missing_fields_are_absent_not_guessed(self):
        # Only a title + memory: clocks/bus must simply be absent, never invented.
        html = _tpu_gpu_html("NVIDIA GeForce RTX 3050", [("Memory Size", "8 GB")])
        specs = parse_techpowerup_detail(html, "GPU", "https://tpu/w")
        assert specs["gpu_memory_size"] == 8 * 1024
        assert "gpu_base_clock" not in specs
        assert "gpu_boost_clock" not in specs
        assert "gpu_bus_interface" not in specs
