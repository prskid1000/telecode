// Simulation of the adaptive draft-length policy (common_spec_policy copied from patches/llama.cpp 0003; re-copy if it changes).
// Build: cl /EHsc /std:c++17 spec_policy_sim.cpp   Expect: OK

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

struct common_spec_policy {
    int   k_max;
    float min_gain;
    float ema;
    int   probe_every;

    std::vector<double> p;       // [k_max] acceptance of position i given the positions before it
    std::vector<double> cost_ms; // [k_max + 1] round time by window size t = k + 1, index 0 unused
    std::vector<int>    cost_n;
    int rounds = 0;
    int since_probe = 0;
    int probe_dir = 1;
    int zero_calls = 0;
    int n_min = 0; // the smallest draft the drafter keeps

    common_spec_policy(int k_max, float min_gain, float ema, int probe_every)
        : k_max(k_max), min_gain(min_gain), ema(ema), probe_every(probe_every),
          p(k_max, 0.7), cost_ms(k_max + 2, 0.0), cost_n(k_max + 2, 0) {}

    // time of a round with window t: measured, else scaled from the nearest measured size with a guessed slope
    double cost(int t) const {
        constexpr double slope = 0.25;
        if (cost_n[t] > 0) {
            return cost_ms[t];
        }
        int a = -1;
        int b = -1; // the two measured sizes nearest to t
        for (int u = 1; u <= k_max + 1; ++u) {
            if (cost_n[u] == 0) {
                continue;
            }
            if (a < 0 || std::abs(u - t) < std::abs(a - t)) {
                b = a;
                a = u;
            } else if (b < 0 || std::abs(u - t) < std::abs(b - t)) {
                b = u;
            }
        }
        if (a < 0) {
            return 1.0 + slope*(t - 1);
        }
        if (b < 0) {
            return cost_ms[a]*(1.0 + slope*(t - 1))/(1.0 + slope*(a - 1));
        }
        // a line through the two nearest measured sizes, which also prices the round that drafts nothing (t = 1), never below a fifth of the nearer
        const double c = cost_ms[a] + (cost_ms[b] - cost_ms[a])*(double) (t - a)/(double) (b - a);
        return std::max(c, 0.2*cost_ms[a]);
    }

    // expected tokens of a round with k drafts: the verify always commits one
    double expected(int k) const {
        double e = 1.0;
        double run = 1.0;
        for (int i = 0; i < k; ++i) {
            run *= p[i];
            e += run;
        }
        return e;
    }

    // the length for the next round, 0 = do not draft
    int choose() {
        // the first rounds measure the longest window
        if (rounds < 4) {
            return k_max;
        }
        const double base = 1.0/cost(1);
        int    best_k    = 0;
        double best_rate = base*(1.0 + min_gain);
        for (int k = 1; k <= k_max; ++k) {
            const double rate = expected(k)/cost(k + 1);
            if (rate > best_rate) {
                best_rate = rate;
                best_k    = k;
            }
        }
        if (probe_every > 0 && ++since_probe >= probe_every) {
            // a size that was never or rarely measured has only a guessed cost: try one next to the best
            since_probe = 0;
            probe_dir   = -probe_dir;
            const int k = std::clamp(best_k + probe_dir, 1, k_max);
            if (cost_n[k + 1] < 3) {
                return k;
            }
        }
        // with nothing drafted nothing is observed, so drafting is tried again now and then
        if (best_k == 0) {
            if (++zero_calls >= 32) {
                zero_calls = 0;
                return std::max(1, n_min);
            }
        } else {
            zero_calls = 0;
        }
        return best_k;
    }

    void observe(int k, int accepted, double round_ms) {
        if (k <= 0) {
            return;
        }
        accepted = std::clamp(accepted, 0, k);
        // positions before `accepted` were accepted, the one at `accepted` (if drafted) was rejected
        const int seen = std::min(k, accepted + 1);
        for (int i = 0; i < seen; ++i) {
            const double hit = i < accepted ? 1.0 : 0.0;
            p[i] += ema*(hit - p[i]);
        }
        // all accepted: the positions beyond were not tested, pull them toward the deepest tested one so the window can grow
        if (accepted == k && k < k_max) {
            for (int i = k; i < k_max; ++i) {
                p[i] += ema*(p[k - 1] - p[i]);
            }
        }
        const int t = k + 1;
        if (round_ms > 0.0) {
            cost_ms[t] = cost_n[t] > 0 ? (1.0 - ema)*cost_ms[t] + ema*round_ms : round_ms;
            cost_n[t]++;
        }
        rounds++;
    }
};

static int g_fail = 0;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); g_fail++; } } while (0)

// a world: per-position acceptance p, a round of window t costs base + slope*(t-1) ms
static int simulate(common_spec_policy & pol, const std::vector<double> & p, double base, double slope, int rounds, std::mt19937 & rng,
                    std::vector<int> & chosen) {
    std::uniform_real_distribution<double> u(0.0, 1.0);
    std::normal_distribution<double> noise(1.0, 0.03);
    int last = -1;
    for (int r = 0; r < rounds; ++r) {
        int k = pol.choose();
        if (pol.n_min > 0 && k > 0) {
            k = std::max(k, pol.n_min);
        }
        chosen.push_back(k);
        last = k;
        if (k == 0) {
            continue; // nothing is drafted, nothing is observed
        }
        int acc = 0;
        while (acc < k && u(rng) < p[acc]) {
            acc++;
        }
        pol.observe(k, acc, (base + slope*k)*noise(rng));
    }
    return last;
}

// the length with the best expected tokens per ms in that world
static int best_k(const std::vector<double> & p, double base, double slope, int k_max) {
    double best = 1.0/base;
    int bk = 0;
    for (int k = 1; k <= k_max; ++k) {
        double e = 1.0, run = 1.0;
        for (int i = 0; i < k; ++i) {
            run *= p[i];
            e += run;
        }
        const double rate = e/(base + slope*k);
        if (rate > best) {
            best = rate;
            bk = k;
        }
    }
    return bk;
}

static double mean_tail(const std::vector<int> & v, int n) {
    double s = 0;
    for (size_t i = v.size() - n; i < v.size(); ++i) {
        s += v[i];
    }
    return s/n;
}

int main() {
    std::mt19937 rng(7);
    struct world { const char * name; double p; double base; double slope; };
    const world worlds[] = {
        {"high acceptance, cheap verify",  0.92, 20.0, 1.5},
        {"high acceptance, dear verify",   0.92, 20.0, 9.0},
        {"medium acceptance",              0.70, 20.0, 3.0},
        {"low acceptance, cheap verify",   0.35, 20.0, 1.0},
        {"low acceptance, dear verify",    0.25, 20.0, 12.0},
    };
    for (const world & w : worlds) {
        const int k_max = 6;
        std::vector<double> p(k_max, w.p);
        for (int i = 1; i < k_max; ++i) {
            p[i] = p[i - 1]*0.97; // a little lower for the deeper positions
        }
        common_spec_policy pol(k_max, 0.03f, 0.1f, 16);
        std::vector<int> chosen;
        simulate(pol, p, w.base, w.slope, 3000, rng, chosen);
        const int want = best_k(p, w.base, w.slope, k_max);
        const double got = mean_tail(chosen, 1000);
        // the policy probes its neighbours now and then, so compare the average with some room
        printf("%-32s best k %d, policy settles around %.2f\n", w.name, want, got);
        CHECK(std::abs(got - want) <= 1.6, "policy %.2f is too far from the best length %d", got, want);
        if (want == 0) {
            CHECK(std::count(chosen.begin() + 2000, chosen.end(), 0) > 0, "never settles on not drafting where it does not pay");
        }
    }
    // never stuck at zero: even when drafting does not pay, it tries again
    {
        std::vector<double> p(4, 0.02);
        common_spec_policy pol(4, 0.03f, 0.1f, 0);
        std::vector<int> chosen;
        simulate(pol, p, 20.0, 30.0, 2000, rng, chosen);
        int tries = (int) std::count_if(chosen.begin(), chosen.end(), [](int k) { return k > 0; });
        printf("hopeless drafts: %d of %zu rounds still try a draft\n", tries, chosen.size());
        CHECK(tries >= 2000/40, "the policy stopped trying to draft (%d tries)", tries);
        CHECK(tries <= 2000/4, "the policy keeps drafting although it never pays (%d tries)", tries);
    }
    // n_min is respected
    {
        std::vector<double> p(4, 0.9);
        common_spec_policy pol(4, 0.03f, 0.1f, 8);
        pol.n_min = 2;
        std::vector<int> chosen;
        simulate(pol, p, 20.0, 2.0, 500, rng, chosen);
        CHECK(std::all_of(chosen.begin(), chosen.end(), [](int k) { return k == 0 || k >= 2; }), "a draft shorter than n_min");
    }
    printf("%s\n", g_fail ? "FAILED" : "OK");
    return g_fail ? 1 : 0;
}
