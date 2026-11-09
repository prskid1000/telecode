"""Model-load tuning helpers (llamacpp/load_bench.py) and the RAM reading of an unload (llamacpp/ram.py): no GPU, no model."""
from llamacpp import load_bench as lb
from llamacpp import ram

LOG = """\
0.02.364.933 W load_all_data: read 1.99 GiB from D:\\m.gguf in 1.27 s (1602 MiB/s, 8 readers x 32 MiB, unbuffered)
0.10.480.800 W load_all_data: read 18.82 GiB from D:\\m.gguf in 8.11 s (2376 MiB/s, 4 readers x 32 MiB, unbuffered)
0.12.195.906 W load_tensors: read 857.61 MiB in 0.37 s (2347 MiB/s, 4 readers x 32 MiB, unbuffered)
"""


def test_parse_load_log_sums_every_read():
    r = lb.parse_load_log(LOG)
    assert r["read_gib"] == round(1.99 + 18.82 + 857.61 / 1024, 2)
    assert r["read_s"] == round(1.27 + 8.11 + 0.37, 2)
    assert r["readers"] == 8 and r["chunk_mb"] == 32
    assert r["mib_s"] == round(r["read_gib"] * 1024 / r["read_s"])


def test_parse_load_log_old_line_without_chunk_and_no_lines():
    r = lb.parse_load_log("x: read 2.00 GiB from a in 1.00 s (2048 MiB/s, 2 readers, unbuffered)")
    assert r["read_gib"] == 2.0 and r["chunk_mb"] is None
    assert lb.parse_load_log("llama-server started with the ordinary reader") == {}


def _res(r, c, *mib):
    return {"readers": r, "chunk_mb": c, "mib_s": list(mib), "mean": round(sum(mib) / len(mib))}


def test_choose_ignores_a_single_lucky_run_and_prefers_fewer_readers_when_close():
    results = [_res(2, 64, 2713),               # measured once: may not win
               _res(8, 64, 2763, 2763, 2763),
               _res(4, 64, 2727, 2749, 2738)]   # within 3 % of the best and fewer readers
    best = lb.choose(results)
    assert (best["readers"], best["chunk_mb"]) == (4, 64)


def test_choose_takes_the_clear_winner():
    results = [_res(2, 16, 2000, 2010), _res(8, 64, 2600, 2620)]
    assert lb.choose(results)["readers"] == 8


def test_env_for_maps_the_model_settings():
    assert lb.env_for({}) == {}
    assert lb.env_for({"load_readers": 4, "load_chunk_mb": 64.0}) == {
        "LLAMA_FAST_READ_THREADS": "4", "LLAMA_FAST_READ_CHUNK_MB": "64"}
    assert lb.env_for({"load_readers": 0, "load_chunk_mb": 0}) == {}
    assert lb.env_for({"fast_load": False, "load_readers": 4}) == {"LLAMA_FAST_READ": "0"}


def test_ram_release_text_and_warning():
    assert ram.describe_release(22528, 10000, 32000) == ("RAM freed: 21.5 GiB, process used 22.0 GiB", False)
    text, held = ram.describe_release(14000, 10000, 11000)
    assert held and text.startswith("RAM freed: 1.0 GiB")
    assert ram.describe_release(None, 1, 2) == ("RAM freed: unknown", False)
    assert ram.describe_release(500, 1000, 1000)[1] is False   # under 1 GiB held: never warns
