// Randomised invariant test of moe_cache_lru (the planning logic of the MoE cache), no GPU, no model.
// It includes the implementation file so the anonymous-namespace struct is reachable.
#include "llama-moe-cache.cpp"

#include <cstdio>
#include <cstdlib>
#include <random>

static int g_fail = 0;
#define CHECK(c, ...) do { if (!(c)) { if (g_fail < 20) { printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } g_fail++; } } while (0)

struct fuzz {
    static constexpr int n_layer  = 5;
    static constexpr int n_expert = 24;
    int n_slots = 0;
    int n_stage = 0;
    moe_cache_lru lru;
    std::vector<std::vector<int32_t>> maps;
    llama_moe_cache_params p;
    std::vector<moe_cache_lru::fill> fills;
    std::vector<std::pair<int32_t, moe_cache_lru::fill>> round_fills;
    std::mt19937 rng;

    explicit fuzz(uint32_t seed, int slots, int stage) : n_slots(slots), n_stage(stage), rng(seed) {
        lru.init(n_layer, n_expert, n_slots - n_stage);
        lru.n_layers_group = n_layer;
        maps.assign(n_layer, std::vector<int32_t>(n_expert, -1));
        for (int il = 0; il < n_layer; ++il) {
            lru.slot_map[il] = maps[il].data();
        }
        std::uniform_int_distribution<int> d(0, 3);
        p.promote_after = 1 + d(rng);
        p.promote_max   = d(rng);
        p.promote_gain  = d(rng) == 0 ? 0.5f : 0.0f;
        p.evict_window  = d(rng) * 3;
        p.pcie_share    = d(rng) == 0 ? 0.0f : 0.4f;
        p.pcie_max      = 1 + d(rng);
        p.decay         = d(rng) * 8;
        p.swap_every    = d(rng) == 0 ? 2 : 0;
        p.swap_max      = d(rng) * 4;
    }

    // every slot is in the list once, every mapping and every key agree
    void check(const char * what, int il_checked) {
        const int ns = lru.n_slots;
        std::vector<int> seen_in_list(ns, 0);
        int count = 0;
        for (int s = lru.head; s >= 0 && count <= ns; s = lru.next[s]) {
            seen_in_list[s]++;
            count++;
        }
        CHECK(count == ns, "%s: list has %d of %d slots", what, count, ns);
        for (int s = 0; s < ns; ++s) {
            CHECK(seen_in_list[s] == 1, "%s: slot %d is %d times in the list", what, s, seen_in_list[s]);
        }
        std::vector<int> owner(ns, 0);
        for (int il = 0; il < n_layer; ++il) {
            for (int e = 0; e < n_expert; ++e) {
                const int32_t s = maps[il][e];
                if (s < 0) {
                    continue;
                }
                if (s >= ns) {
                    CHECK(n_stage > 0 && s < n_slots, "%s: layer %d expert %d has slot %d out of range", what, il, e, s);
                    continue;
                }
                owner[s]++;
                CHECK(lru.key_of[s] == il*n_expert + e, "%s: slot %d is mapped to (%d,%d) but its key is %d", what, s, il, e, lru.key_of[s]);
            }
        }
        for (int s = 0; s < ns; ++s) {
            CHECK(owner[s] <= 1, "%s: slot %d is mapped by %d experts", what, s, owner[s]);
        }
        // a slot with a key is either mapped or pending
        for (int s = 0; s < ns; ++s) {
            const int32_t k = lru.key_of[s];
            if (k < 0) {
                continue;
            }
            const int il = k / n_expert, e = k % n_expert;
            bool pend = false;
            for (const auto & f : lru.pending[il]) {
                pend |= f.expert == e && f.slot == s;
            }
            CHECK(maps[il][e] == s || pend, "%s: slot %d holds (%d,%d) that is neither mapped nor pending", what, s, il, e);
        }
        (void) il_checked;
    }

    std::vector<int32_t> random_ids(int n_tokens, int n_used) {
        std::vector<int32_t> ids;
        std::uniform_int_distribution<int> hot(0, 7), any(0, n_expert - 1), coin(0, 2);
        for (int t = 0; t < n_tokens; ++t) {
            std::vector<int32_t> row;
            while ((int) row.size() < n_used) {
                const int e = coin(rng) ? hot(rng) : any(rng);
                if (std::find(row.begin(), row.end(), e) == row.end()) {
                    row.push_back(e);
                }
            }
            ids.insert(ids.end(), row.begin(), row.end());
        }
        return ids;
    }

    void step() {
        std::uniform_int_distribution<int> dil(0, n_layer - 1), dop(0, 9), dtok(1, 4);
        const int il = dil(rng);
        const int n_used = 4;
        const int op = dop(rng);
        lru.clear_staging(il, lru.n_slots);
        if (op < 6) {
            const auto ids = random_ids(dtok(rng), n_used);
            // the experts this call already has in the cache must still be there after it, they are read by the GPU
            std::vector<std::pair<int, int32_t>> before;
            for (int32_t e : ids) {
                if (maps[il][e] >= 0) {
                    before.push_back({ e, maps[il][e] });
                }
            }
            size_t n_hit = 0;
            const size_t n_cpu = lru.plan_cpu_miss(il, ids.data(), ids.size(), p, p.pcie_share, fills, n_hit);
            (void) n_cpu;
            check("plan_cpu_miss", il);
            for (auto & b : before) {
                CHECK(maps[il][b.first] == b.second, "plan_cpu_miss evicted a hit: layer %d expert %d slot %d -> %d", il, b.first, b.second, maps[il][b.first]);
            }
            for (const auto & f : fills) {
                CHECK(f.slot >= 0 && f.slot < lru.n_slots, "fill slot %d out of range", f.slot);
                if (f.now) {
                    CHECK(maps[il][f.expert] == f.slot, "immediate fill (%d,%d) is not mapped", il, f.expert);
                }
            }
            if (lru.round_due(p)) {
                lru.plan_round(p, round_fills);
                check("plan_round", il);
            }
        } else if (op == 6 && il + 1 < n_layer) {
            const auto ids = random_ids(1, n_used);
            // plan_ahead must not take a slot of the current call: simulate the current call first
            const auto cur = random_ids(2, n_used);
            size_t n_hit = 0;
            lru.plan_cpu_miss(il, cur.data(), cur.size(), p, p.pcie_share, fills, n_hit);
            std::vector<std::pair<int, int32_t>> before;
            for (int32_t e : cur) {
                if (maps[il][e] >= 0) {
                    before.push_back({ e, maps[il][e] });
                }
            }
            lru.plan_ahead(il + 1, ids.data(), ids.size(), 2, p.evict_window, fills);
            check("plan_ahead", il + 1);
            for (auto & b : before) {
                CHECK(maps[il][b.first] == b.second, "plan_ahead evicted a slot the running layer uses: layer %d expert %d", il, b.first);
            }
        } else if (op == 7 && n_stage > 0) {
            const auto ids = random_ids(40, n_used);
            size_t n_hit = 0;
            const size_t n_cpu = lru.plan_prefill(il, ids.data(), ids.size(), 0.5f, n_stage, lru.n_slots, fills, n_hit);
            (void) n_cpu;
            check("plan_prefill", il);
            for (const auto & f : fills) {
                CHECK(f.slot >= lru.n_slots && f.slot < n_slots, "staging fill slot %d is not a staging slot", f.slot);
            }
            // the next call of this layer, of any kind, starts without the staging entries
            lru.clear_staging(il, lru.n_slots);
            for (int e = 0; e < n_expert; ++e) {
                CHECK(maps[il][e] < lru.n_slots, "layer %d expert %d still in a staging slot after clear", il, e);
            }
        } else {
            const auto ids = random_ids(dtok(rng), n_used);
            size_t n_hit = 0;
            lru.plan(il, ids.data(), ids.size(), fills, n_hit);
            check("plan", il);
        }
    }
};

int main() {
    int total = 0;
    for (uint32_t seed = 1; seed <= 400; ++seed) {
        const int slots = 8 + (int) (seed % 5)*4;
        const int stage = seed % 3 == 0 ? 4 : 0;
        fuzz f(seed, slots + stage, stage);
        for (int i = 0; i < 400 && g_fail == 0; ++i) {
            f.step();
            total++;
        }
        if (g_fail) {
            printf("seed %u (slots %d stage %d) failed after %d steps\n", seed, slots, stage, total);
            break;
        }
    }
    printf("%s: %d steps, %d failures\n", g_fail ? "FAILED" : "OK", total, g_fail);
    return g_fail ? 1 : 0;
}
