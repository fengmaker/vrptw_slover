#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace py = pybind11;
using i64 = std::int64_t;
using Route = std::vector<int>;
using Routes = std::vector<Route>;
using Edge = std::pair<int, int>;

namespace {
constexpr i64 kMaxInput = 1'000'000'000'000LL;
constexpr std::size_t kMaxCustomers = 100'000;
using Clock = std::chrono::steady_clock;

struct Move { std::string kind; int a; int ia; int b; int ib; };
struct Stats {
    i64 candidates = 0, feasible_candidates = 0, infeasible_candidates = 0;
    i64 route_evaluations = 0, incremental_route_evaluations = 0;
    i64 reused_prefix_customers = 0, reused_suffix_customers = 0;
    i64 neighbour_filtered = 0, rejected_capacity = 0, rejected_time_window = 0;
    i64 rejected_depot_close = 0, rejected_empty_route = 0;
    py::dict as_dict() const {
        py::dict d;
        d["candidates"] = candidates; d["feasible_candidates"] = feasible_candidates;
        d["infeasible_candidates"] = infeasible_candidates; d["route_evaluations"] = route_evaluations;
        d["incremental_route_evaluations"] = incremental_route_evaluations;
        d["reused_prefix_customers"] = reused_prefix_customers;
        d["reused_suffix_customers"] = reused_suffix_customers;
        d["neighbour_filtered"] = neighbour_filtered; d["rejected_capacity"] = rejected_capacity;
        d["rejected_time_window"] = rejected_time_window; d["rejected_depot_close"] = rejected_depot_close;
        d["rejected_empty_route"] = rejected_empty_route;
        return d;
    }
};
struct FastResult {
    i64 load = 0, distance = 0, return_time = 0;
    bool capacity = false, time_window = false, depot_close = false;
    int reused_prefix = 0, reused_suffix = 0;
    bool feasible() const { return !capacity && !time_window && !depot_close; }
};
struct RouteCache {
    Route route;
    i64 distance = 0, load = 0;
    std::vector<i64> prefix_departure, prefix_load, prefix_distance;
    std::vector<i64> suffix_distance, suffix_load, duration, release, latest;
};
bool known_operator(const std::string& kind) {
    return kind == "relocate" || kind == "swap" || kind == "two_opt" ||
           kind == "two_opt_star" || kind == "relocate_pair" ||
           kind == "exchange_pair_single" || kind == "exchange_pairs";
}
Edge canonical_edge(int left, int right) {
    return left <= right ? Edge{left, right} : Edge{right, left};
}
}  // namespace

class MoveEvaluator {
public:
    MoveEvaluator(std::vector<std::vector<i64>> distance, std::vector<i64> demand,
                  std::vector<i64> ready, std::vector<i64> due, std::vector<i64> service,
                  i64 capacity, Routes routes)
        : distance_(std::move(distance)), demand_(std::move(demand)), ready_(std::move(ready)),
          due_(std::move(due)), service_(std::move(service)), capacity_(capacity), routes_(std::move(routes)) {
        validate_inputs();
        caches_.reserve(routes_.size());
        for (const auto& route : routes_) caches_.push_back(build_cache(route));
    }

    py::object delta(const std::string& kind, i64 a, i64 ia, i64 b, i64 ib) const {
        const Move move = checked_move(kind, a, ia, b, ib);
        const Routes changed = changed_routes(move);
        const auto affected = affected_indices(move);
        for (std::size_t i = 0; i < changed.size(); ++i) {
            if (routes_[static_cast<std::size_t>(affected[i])].empty() || changed[i].empty()) return py::none();
        }
        bool capacity = false, time_window = false, depot_close = false;
        i64 value = 0;
        for (std::size_t i = 0; i < changed.size(); ++i) {
            const auto result = evaluate(caches_[affected[i]], changed[i]);
            capacity = capacity || result.capacity; time_window = time_window || result.time_window;
            depot_close = depot_close || result.depot_close;
            value += result.distance - caches_[affected[i]].distance;
        }
        if (capacity || time_window || depot_close) return py::none();
        return py::cast(value);
    }

    Routes apply(const std::string& kind, i64 a, i64 ia, i64 b, i64 ib) {
        const Move move = checked_move(kind, a, ia, b, ib);
        const Routes changed = changed_routes(move);
        const auto affected = affected_indices(move);
        for (std::size_t i = 0; i < changed.size(); ++i) {
            if (routes_[static_cast<std::size_t>(affected[i])].empty() || changed[i].empty())
                throw std::invalid_argument("move touches or creates an empty route");
        }
        std::vector<RouteCache> replacements;
        replacements.reserve(changed.size());
        for (std::size_t i = 0; i < changed.size(); ++i) {
            const auto verdict = evaluate(caches_[affected[i]], changed[i]);
            if (!verdict.feasible()) throw std::invalid_argument("move would make a route infeasible");
            replacements.push_back(build_cache(changed[i]));
        }
        Routes result = routes_;
        for (std::size_t i = 0; i < changed.size(); ++i) {
            result[static_cast<std::size_t>(affected[i])] = changed[i];
            caches_[affected[i]] = std::move(replacements[i]);
        }
        routes_ = std::move(result);
        return routes_;
    }

    py::dict scan(const std::vector<std::string>& operators, const std::string& strategy,
                  py::object remaining_seconds, py::object neighbours) const {
        if (operators.empty()) throw std::invalid_argument("operators must be nonempty");
        for (const auto& kind : operators) if (!known_operator(kind))
            throw std::invalid_argument("unknown operator: " + kind);
        if (strategy != "first" && strategy != "best")
            throw std::invalid_argument("strategy must be 'first' or 'best'");
        const bool has_deadline = !remaining_seconds.is_none();
        double seconds = 0;
        if (has_deadline) {
            try { seconds = py::cast<double>(remaining_seconds); }
            catch (const py::cast_error&) { throw std::invalid_argument("remaining_seconds must be a nonnegative finite number or None"); }
            if (!std::isfinite(seconds) || seconds < 0)
                throw std::invalid_argument("remaining_seconds must be a nonnegative finite number or None");
        }
        std::vector<std::unordered_set<int>> adjacency;
        const bool filter = !neighbours.is_none();
        if (filter) {
            py::iterable rows;
            try { rows = py::reinterpret_borrow<py::iterable>(neighbours); }
            catch (const py::cast_error&) { throw std::invalid_argument("neighbours must be a sequence of customer-index sequences or None"); }
            adjacency.resize(demand_.size());
            std::size_t row_index = 0;
            for (py::handle row_handle : rows) {
                if (row_index >= adjacency.size()) throw std::invalid_argument("neighbours must contain one row per depot/customer index");
                py::iterable row;
                try { row = py::reinterpret_borrow<py::iterable>(row_handle); }
                catch (const py::cast_error&) { throw std::invalid_argument("each neighbour row must be iterable"); }
                for (py::handle item : row) {
                    int node;
                    try { node = py::cast<int>(item); }
                    catch (const py::cast_error&) { throw std::invalid_argument("neighbour indices must be integers"); }
                    if (node < 0 || static_cast<std::size_t>(node) >= demand_.size())
                        throw std::invalid_argument("neighbour customer index out of range");
                    adjacency[row_index].insert(node);
                }
                ++row_index;
            }
            if (row_index != adjacency.size()) throw std::invalid_argument("neighbours must contain one row per depot/customer index");
        }

        Stats stats;
        Move best{"", 0, 0, 0, 0};
        i64 best_delta = 0;
        bool found = false, timed_out = false;
        const auto started = Clock::now();
        auto visit = [&](const Move& move) -> bool {
            if (has_deadline && std::chrono::duration<double>(Clock::now() - started).count() >= seconds) {
                timed_out = true; return false;
            }
            if (filter && !allows_move(move, adjacency)) { ++stats.neighbour_filtered; return true; }
            i64 value = 0;
            if (evaluate_move(move, value, stats) && value < best_delta) {
                best = move; best_delta = value; found = true;
                if (strategy == "first") return false;
            }
            return true;
        };
        for (const auto& kind : operators) {
            if (!enumerate_kind(kind, visit)) break;
            if (timed_out || (strategy == "first" && found)) break;
        }
        py::dict result;
        if (timed_out) {
            result["move"] = py::none(); result["delta"] = 0; result["timed_out"] = true;
        } else if (found) {
            result["move"] = py::make_tuple(best.kind, best.a, best.ia, best.b, best.ib);
            result["delta"] = best_delta; result["timed_out"] = false;
        } else {
            result["move"] = py::none(); result["delta"] = 0; result["timed_out"] = false;
        }
        result["stats"] = stats.as_dict();
        return result;
    }

private:
    std::vector<std::vector<i64>> distance_;
    std::vector<i64> demand_, ready_, due_, service_;
    i64 capacity_;
    Routes routes_;
    mutable std::vector<RouteCache> caches_;

    void validate_inputs() {
        const std::size_t n = demand_.size();
        if (n == 0 || n - 1 > kMaxCustomers) throw std::invalid_argument("customer vectors must contain depot 0 and at most 100000 customers");
        if (routes_.size() > kMaxCustomers || ready_.size() != n || due_.size() != n ||
            service_.size() != n || distance_.size() != n)
            throw std::invalid_argument("matrix and customer vectors must have matching dimensions");
        if (capacity_ < 0 || capacity_ > kMaxInput) throw std::invalid_argument("capacity is outside the supported nonnegative input bound");
        for (std::size_t i = 0; i < n; ++i) {
            if (distance_[i].size() != n) throw std::invalid_argument("distance matrix must be square");
            if (demand_[i] < 0 || demand_[i] > kMaxInput || ready_[i] < 0 || ready_[i] > kMaxInput ||
                due_[i] < ready_[i] || due_[i] > kMaxInput || service_[i] < 0 || service_[i] > kMaxInput)
                throw std::invalid_argument("customer fields are outside the supported input bounds");
            for (i64 value : distance_[i]) if (value < 0 || value > kMaxInput)
                throw std::invalid_argument("distance entries are outside the supported nonnegative input bound");
        }
        if (demand_[0] != 0 || service_[0] != 0) throw std::invalid_argument("depot demand and service must be zero");
        std::vector<bool> seen(n, false);
        for (const auto& route : routes_) for (int node : route) {
            if (node <= 0 || static_cast<std::size_t>(node) >= n) throw std::invalid_argument("route customer index out of range");
            if (seen[static_cast<std::size_t>(node)]) throw std::invalid_argument("routes must contain each customer at most once");
            seen[static_cast<std::size_t>(node)] = true;
        }
        for (std::size_t node = 1; node < n; ++node) if (!seen[node])
            throw std::invalid_argument("routes must contain every customer exactly once");
    }

    Move checked_move(const std::string& kind, i64 a, i64 ia, i64 b, i64 ib) const {
        if (!known_operator(kind)) throw std::invalid_argument("unknown move kind: " + kind);
        if (a < 0 || b < 0 || static_cast<std::uint64_t>(a) >= routes_.size() || static_cast<std::uint64_t>(b) >= routes_.size())
            throw std::invalid_argument("move route index out of range");
        if (a > std::numeric_limits<int>::max() || b > std::numeric_limits<int>::max() ||
            ia < 0 || ia > std::numeric_limits<int>::max() || ib < 0 || ib > std::numeric_limits<int>::max())
            throw std::invalid_argument("move position out of range");
        if (static_cast<std::uint64_t>(ia) > routes_[static_cast<std::size_t>(a)].size() ||
            static_cast<std::uint64_t>(ib) > routes_[static_cast<std::size_t>(b)].size())
            throw std::invalid_argument("move position out of range");
        Move move{kind, static_cast<int>(a), static_cast<int>(ia), static_cast<int>(b), static_cast<int>(ib)};
        (void)changed_routes(move);  // central structural and position validation
        return move;
    }
    static std::vector<int> affected_indices(const Move& move) {
        return move.a == move.b ? std::vector<int>{move.a} : std::vector<int>{move.a, move.b};
    }
    Routes changed_routes(const Move& move) const {
        Routes local{routes_[static_cast<std::size_t>(move.a)]};
        const bool same = move.a == move.b;
        if (!same) local.push_back(routes_[static_cast<std::size_t>(move.b)]);
        Move m{move.kind, 0, move.ia, same ? 0 : 1, move.ib};
        return apply_structural(local, m);
    }

    Routes apply_structural(const Routes& base, const Move& m) const {
        Routes result = base;
        Route& a = result.at(static_cast<std::size_t>(m.a));
        Route& b = result.at(static_cast<std::size_t>(m.b));
        const int ia = m.ia, ib = m.ib;
        if (m.kind == "relocate") {
            const int last = static_cast<int>(b.size()) - (m.a == m.b ? 1 : 0);
            if (ia >= static_cast<int>(a.size()) || ib > last) throw std::invalid_argument("relocate position out of range");
            const int node = a[static_cast<std::size_t>(ia)];
            a.erase(a.begin() + ia); b.insert(b.begin() + ib, node);
        } else if (m.kind == "swap") {
            if (ia >= static_cast<int>(a.size()) || ib >= static_cast<int>(b.size())) throw std::invalid_argument("swap position out of range");
            std::swap(a[static_cast<std::size_t>(ia)], b[static_cast<std::size_t>(ib)]);
        } else if (m.kind == "two_opt") {
            if (m.a != m.b) throw std::invalid_argument("two_opt must stay within one route");
            if (ia >= ib || ib > static_cast<int>(a.size()) || ib - ia < 2) throw std::invalid_argument("two_opt segment must contain at least two customers");
            std::reverse(a.begin() + ia, a.begin() + ib);
        } else if (m.kind == "two_opt_star") {
            if (m.a == m.b) throw std::invalid_argument("two_opt_star needs two routes");
            if (ia > static_cast<int>(a.size()) || ib > static_cast<int>(b.size())) throw std::invalid_argument("two_opt_star cut out of range");
            Route tail_a(a.begin() + ia, a.end()), tail_b(b.begin() + ib, b.end());
            a.erase(a.begin() + ia, a.end()); b.erase(b.begin() + ib, b.end());
            a.insert(a.end(), tail_b.begin(), tail_b.end()); b.insert(b.end(), tail_a.begin(), tail_a.end());
        } else if (m.kind == "relocate_pair") {
            const int last = static_cast<int>(b.size()) - (m.a == m.b ? 2 : 0);
            if (ia + 1 >= static_cast<int>(a.size()) || ib > last) throw std::invalid_argument("relocate_pair position out of range");
            Route segment(a.begin() + ia, a.begin() + ia + 2);
            a.erase(a.begin() + ia, a.begin() + ia + 2); b.insert(b.begin() + ib, segment.begin(), segment.end());
        } else if (m.kind == "exchange_pair_single" || m.kind == "exchange_pairs") {
            const int size_b = m.kind == "exchange_pair_single" ? 1 : 2;
            if (ia + 1 >= static_cast<int>(a.size()) || ib + size_b > static_cast<int>(b.size()))
                throw std::invalid_argument(m.kind + " position out of range");
            if (m.a != m.b) {
                Route sa(a.begin() + ia, a.begin() + ia + 2), sb(b.begin() + ib, b.begin() + ib + size_b);
                a.erase(a.begin() + ia, a.begin() + ia + 2); a.insert(a.begin() + ia, sb.begin(), sb.end());
                b.erase(b.begin() + ib, b.begin() + ib + size_b); b.insert(b.begin() + ib, sa.begin(), sa.end());
            } else if (ia + 2 <= ib) {
                Route next;
                next.insert(next.end(), a.begin(), a.begin() + ia);
                next.insert(next.end(), a.begin() + ib, a.begin() + ib + size_b);
                next.insert(next.end(), a.begin() + ia + 2, a.begin() + ib);
                next.insert(next.end(), a.begin() + ia, a.begin() + ia + 2);
                next.insert(next.end(), a.begin() + ib + size_b, a.end()); a = std::move(next);
            } else if (ib + size_b <= ia) {
                Route next;
                next.insert(next.end(), a.begin(), a.begin() + ib);
                next.insert(next.end(), a.begin() + ia, a.begin() + ia + 2);
                next.insert(next.end(), a.begin() + ib + size_b, a.begin() + ia);
                next.insert(next.end(), a.begin() + ib, a.begin() + ib + size_b);
                next.insert(next.end(), a.begin() + ia + 2, a.end()); a = std::move(next);
            } else throw std::invalid_argument("exchange segments must not overlap");
        }
        return result;
    }

    RouteCache build_cache(const Route& route) const {
        RouteCache c; c.route = route;
        const std::size_t n = route.size();
        c.prefix_departure.resize(n + 1); c.prefix_load.resize(n + 1); c.prefix_distance.resize(n + 1);
        c.prefix_departure[0] = ready_[0];
        i64 departure = ready_[0]; int previous = 0;
        for (std::size_t i = 0; i < n; ++i) {
            const int node = route[i]; const auto j = static_cast<std::size_t>(node);
            const i64 arrival = departure + distance_[static_cast<std::size_t>(previous)][j];
            const i64 start = std::max(arrival, ready_[j]);
            if (start > due_[j]) throw std::invalid_argument("initial routes must be feasible (time window)");
            departure = start + service_[j]; c.load += demand_[j];
            c.distance += distance_[static_cast<std::size_t>(previous)][j];
            c.prefix_departure[i + 1] = departure; c.prefix_load[i + 1] = c.load; c.prefix_distance[i + 1] = c.distance;
            previous = node;
        }
        if (!route.empty()) {
            c.distance += distance_[static_cast<std::size_t>(previous)][0];
            if (c.load > capacity_) throw std::invalid_argument("initial routes must be feasible (capacity)");
            if (departure + distance_[static_cast<std::size_t>(previous)][0] > due_[0])
                throw std::invalid_argument("initial routes must be feasible (depot close)");
        }
        c.suffix_distance.assign(n + 1, 0); c.suffix_load.assign(n + 1, 0);
        c.duration.assign(n, 0); c.release.assign(n, 0); c.latest.assign(n, 0);
        for (std::size_t rev = n; rev > 0; --rev) {
            const std::size_t i = rev - 1; const int node = route[i];
            const int next = i + 1 < n ? route[i + 1] : 0;
            const auto j = static_cast<std::size_t>(node), k = static_cast<std::size_t>(next);
            const i64 arc = distance_[j][k], step = service_[j] + arc;
            c.suffix_distance[i] = arc + c.suffix_distance[i + 1];
            c.suffix_load[i] = demand_[j] + c.suffix_load[i + 1];
            c.duration[i] = step + (i + 1 < n ? c.duration[i + 1] : 0);
            c.release[i] = std::max(ready_[j] + c.duration[i], i + 1 < n ? c.release[i + 1] : i64{0});
            c.latest[i] = i + 1 < n ? std::min(due_[j], c.latest[i + 1] - step) : due_[j];
        }
        return c;
    }

    FastResult evaluate(const RouteCache& original, const Route& candidate) const {
        FastResult r;
        const std::size_t size = std::min(original.route.size(), candidate.size());
        std::size_t prefix = 0;
        while (prefix < size && original.route[prefix] == candidate[prefix]) ++prefix;
        std::size_t suffix = 0;
        while (suffix < size - prefix && original.route[original.route.size() - 1 - suffix] == candidate[candidate.size() - 1 - suffix]) ++suffix;
        r.reused_prefix = static_cast<int>(prefix); r.reused_suffix = static_cast<int>(suffix);
        i64 departure = original.prefix_departure[prefix];
        r.distance = original.prefix_distance[prefix]; r.load = original.prefix_load[prefix];
        int previous = prefix ? candidate[prefix - 1] : 0;
        for (std::size_t i = prefix; i < candidate.size() - suffix; ++i) {
            const int node = candidate[i]; const auto j = static_cast<std::size_t>(node);
            const i64 arrival = departure + distance_[static_cast<std::size_t>(previous)][j];
            const i64 start = std::max(arrival, ready_[j]);
            if (start > due_[j]) r.time_window = true;
            r.distance += distance_[static_cast<std::size_t>(previous)][j]; r.load += demand_[j];
            departure = start + service_[j]; previous = node;
        }
        if (suffix) {
            const std::size_t i = original.route.size() - suffix; const int first = original.route[i];
            const i64 arc = distance_[static_cast<std::size_t>(previous)][static_cast<std::size_t>(first)];
            const i64 arrival = departure + arc;
            if (arrival > original.latest[i]) r.time_window = true;
            r.return_time = std::max(arrival + original.duration[i], original.release[i]);
            r.distance += arc + original.suffix_distance[i]; r.load += original.suffix_load[i];
        } else if (!candidate.empty()) {
            const i64 arc = distance_[static_cast<std::size_t>(previous)][0];
            r.distance += arc; r.return_time = departure + arc;
        } else r.return_time = ready_[0];
        r.capacity = r.load > capacity_; r.depot_close = !candidate.empty() && r.return_time > due_[0];
        return r;
    }

    bool evaluate_move(const Move& move, i64& delta_value, Stats& stats) const {
        const Routes changed = changed_routes(move);
        const auto affected = affected_indices(move);
        ++stats.candidates;
        for (std::size_t i = 0; i < changed.size(); ++i) if (routes_[static_cast<std::size_t>(affected[i])].empty() || changed[i].empty()) {
            ++stats.infeasible_candidates; ++stats.rejected_empty_route; return false;
        }
        bool capacity = false, time_window = false, depot_close = false;
        i64 total = 0;
        for (std::size_t i = 0; i < changed.size(); ++i) {
            const auto result = evaluate(caches_[affected[i]], changed[i]);
            ++stats.route_evaluations; ++stats.incremental_route_evaluations;
            stats.reused_prefix_customers += result.reused_prefix;
            stats.reused_suffix_customers += result.reused_suffix;
            capacity = capacity || result.capacity; time_window = time_window || result.time_window;
            depot_close = depot_close || result.depot_close;
            total += result.distance - caches_[affected[i]].distance;
        }
        if (capacity || time_window || depot_close) {
            ++stats.infeasible_candidates;
            if (capacity) ++stats.rejected_capacity;
            if (time_window) ++stats.rejected_time_window;
            if (depot_close) ++stats.rejected_depot_close;
            return false;
        }
        ++stats.feasible_candidates; delta_value = total; return true;
    }

    bool allowed_edge(int left, int right, const std::vector<std::unordered_set<int>>& neighbours) const {
        if (left == 0 && right == 0) return false;
        if (left == 0 || right == 0) return true;
        return neighbours[static_cast<std::size_t>(left)].count(right) != 0;
    }
    static std::set<Edge> route_edges(const Route& route) {
        std::set<Edge> edges; int previous = 0;
        for (int node : route) { edges.insert(canonical_edge(previous, node)); previous = node; }
        edges.insert(canonical_edge(previous, 0)); return edges;
    }

    bool allows_move(const Move& move, const std::vector<std::unordered_set<int>>& neighbours) const {
        const Route& source = routes_[static_cast<std::size_t>(move.a)];
        const Route& target = routes_[static_cast<std::size_t>(move.b)];
        const int a = move.a, b = move.b, ia = move.ia, ib = move.ib;
        if (move.kind == "relocate") {
            const int node = source[static_cast<std::size_t>(ia)];
            Route destination = a == b ? source : target;
            if (a == b) destination.erase(destination.begin() + ia);
            const int left = ib > 0 ? destination[static_cast<std::size_t>(ib - 1)] : 0;
            const int right = ib < static_cast<int>(destination.size()) ? destination[static_cast<std::size_t>(ib)] : 0;
            return allowed_edge(left, node, neighbours) || allowed_edge(node, right, neighbours);
        }
        if (move.kind == "swap") {
            const int first = source[static_cast<std::size_t>(ia)], second = target[static_cast<std::size_t>(ib)];
            using Replacements = std::vector<std::pair<int, int>>;
            auto incident = [](const Route& route, int pos, const Replacements& replacements) {
                auto at = [&](int index) {
                    for (const auto& replacement : replacements) if (replacement.first == index) return replacement.second;
                    return route[static_cast<std::size_t>(index)];
                };
                const int node = at(pos), left = pos > 0 ? at(pos - 1) : 0;
                const int right = pos + 1 < static_cast<int>(route.size()) ? at(pos + 1) : 0;
                return std::set<Edge>{canonical_edge(left, node), canonical_edge(node, right)};
            };
            const Replacements none;
            std::set<Edge> old_edges = incident(source, ia, none);
            const auto old_b = incident(target, ib, none); old_edges.insert(old_b.begin(), old_b.end());
            std::set<Edge> new_edges;
            if (a == b) {
                const Replacements replacements{{ia, second}, {ib, first}};
                auto first_new = incident(source, ib, replacements), second_new = incident(source, ia, replacements);
                new_edges.insert(first_new.begin(), first_new.end()); new_edges.insert(second_new.begin(), second_new.end());
            } else {
                auto first_new = incident(target, ib, Replacements{{ib, first}});
                auto second_new = incident(source, ia, Replacements{{ia, second}});
                new_edges.insert(first_new.begin(), first_new.end()); new_edges.insert(second_new.begin(), second_new.end());
            }
            for (const auto& edge : new_edges) if (!old_edges.count(edge) && allowed_edge(edge.first, edge.second, neighbours)) return true;
            return false;
        }
        if (move.kind == "two_opt") {
            const int left = ia > 0 ? source[static_cast<std::size_t>(ia - 1)] : 0;
            const int first = source[static_cast<std::size_t>(ia)], last = source[static_cast<std::size_t>(ib - 1)];
            const int right = ib < static_cast<int>(source.size()) ? source[static_cast<std::size_t>(ib)] : 0;
            return allowed_edge(left, last, neighbours) || allowed_edge(first, right, neighbours);
        }
        if (move.kind == "two_opt_star") {
            const int left_a = ia > 0 ? source[static_cast<std::size_t>(ia - 1)] : 0;
            const int first_b = ib < static_cast<int>(target.size()) ? target[static_cast<std::size_t>(ib)] : 0;
            const int left_b = ib > 0 ? target[static_cast<std::size_t>(ib - 1)] : 0;
            const int first_a = ia < static_cast<int>(source.size()) ? source[static_cast<std::size_t>(ia)] : 0;
            return allowed_edge(left_a, first_b, neighbours) || allowed_edge(left_b, first_a, neighbours);
        }
        const auto affected = affected_indices(move);
        Routes old_routes;
        for (int index : affected) old_routes.push_back(routes_[static_cast<std::size_t>(index)]);
        Move local{move.kind, 0, ia, a == b ? 0 : 1, ib};
        const Routes new_routes = apply_structural(old_routes, local);
        std::set<Edge> old_edges, new_edges;
        for (const auto& route : old_routes) { const auto edges = route_edges(route); old_edges.insert(edges.begin(), edges.end()); }
        for (const auto& route : new_routes) { const auto edges = route_edges(route); new_edges.insert(edges.begin(), edges.end()); }
        for (const auto& edge : new_edges) if (!old_edges.count(edge) && allowed_edge(edge.first, edge.second, neighbours)) return true;
        return false;
    }

    template <typename Visitor>
    bool enumerate_kind(const std::string& kind, Visitor& visit) const {
        const int count = static_cast<int>(routes_.size());
        if (kind == "relocate") {
            for (int a = 0; a < count; ++a) {
                const auto& ra = routes_[static_cast<std::size_t>(a)];
                for (int ia = 0; ia < static_cast<int>(ra.size()); ++ia) for (int b = 0; b < count; ++b) {
                    const auto& rb = routes_[static_cast<std::size_t>(b)];
                    if (rb.empty() || (a != b && ra.size() == 1)) continue;
                    const int slots = static_cast<int>(rb.size()) + (a == b ? 0 : 1);
                    for (int ib = 0; ib < slots; ++ib) if (a != b || ia != ib)
                        if (!visit(Move{kind, a, ia, b, ib})) return false;
                }
            }
        } else if (kind == "swap") {
            for (int a = 0; a < count; ++a) for (int b = a; b < count; ++b) {
                const auto& ra = routes_[static_cast<std::size_t>(a)]; const auto& rb = routes_[static_cast<std::size_t>(b)];
                for (int ia = 0; ia < static_cast<int>(ra.size()); ++ia)
                    for (int ib = a == b ? ia + 1 : 0; ib < static_cast<int>(rb.size()); ++ib)
                        if (!visit(Move{kind, a, ia, b, ib})) return false;
            }
        } else if (kind == "two_opt") {
            for (int a = 0; a < count; ++a) {
                const int n = static_cast<int>(routes_[static_cast<std::size_t>(a)].size());
                for (int start = 0; start < n - 1; ++start) for (int end = start + 2; end <= n; ++end)
                    if (!visit(Move{kind, a, start, a, end})) return false;
            }
        } else if (kind == "two_opt_star") {
            for (int a = 0; a < count; ++a) for (int b = a + 1; b < count; ++b) {
                const auto& ra = routes_[static_cast<std::size_t>(a)]; const auto& rb = routes_[static_cast<std::size_t>(b)];
                if (ra.empty() || rb.empty()) continue;
                for (int ca = 0; ca <= static_cast<int>(ra.size()); ++ca) for (int cb = 0; cb <= static_cast<int>(rb.size()); ++cb) {
                    if ((ca == 0 && cb == 0) || (ca == static_cast<int>(ra.size()) && cb == static_cast<int>(rb.size()))) continue;
                    if (!visit(Move{kind, a, ca, b, cb})) return false;
                }
            }
        } else if (kind == "relocate_pair") {
            for (int a = 0; a < count; ++a) {
                const auto& ra = routes_[static_cast<std::size_t>(a)];
                for (int ia = 0; ia < static_cast<int>(ra.size()) - 1; ++ia) for (int b = 0; b < count; ++b) {
                    const auto& rb = routes_[static_cast<std::size_t>(b)];
                    if (rb.empty() || (a != b && ra.size() == 2)) continue;
                    const int slots = static_cast<int>(rb.size()) - (a == b ? 1 : -1);
                    for (int ib = 0; ib < slots; ++ib) if (a != b || ia != ib)
                        if (!visit(Move{kind, a, ia, b, ib})) return false;
                }
            }
        } else {
            const int size_b = kind == "exchange_pair_single" ? 1 : 2;
            for (int a = 0; a < count; ++a) {
                const auto& ra = routes_[static_cast<std::size_t>(a)];
                for (int b = size_b == 2 ? a : 0; b < count; ++b) {
                    const auto& rb = routes_[static_cast<std::size_t>(b)];
                    for (int ia = 0; ia < static_cast<int>(ra.size()) - 1; ++ia)
                        for (int ib = 0; ib <= static_cast<int>(rb.size()) - size_b; ++ib) {
                            if (a == b) {
                                if (!(ia + 2 <= ib || ib + size_b <= ia)) continue;
                                if (size_b == 2 && ib < ia) continue;
                            }
                            if (!visit(Move{kind, a, ia, b, ib})) return false;
                        }
                }
            }
        }
        return true;
    }
};

PYBIND11_MODULE(_native, m) {
    m.doc() = "Native exact feasible VRPTW move evaluator";
#ifdef _MSC_FULL_VER
    const std::string compiler = "MSVC " + std::to_string(_MSC_FULL_VER);
#else
    const std::string compiler = __VERSION__;
#endif
    m.attr("BUILD_INFO") = "compiler=" + compiler + "; cxx_standard=17; api_version=1";
    m.attr("API_VERSION") = 1;
    py::class_<MoveEvaluator>(m, "MoveEvaluator", py::module_local())
        .def(py::init<std::vector<std::vector<i64>>, std::vector<i64>, std::vector<i64>,
                      std::vector<i64>, std::vector<i64>, i64, Routes>(),
             py::arg("distance"), py::arg("demand"), py::arg("ready"), py::arg("due"),
             py::arg("service"), py::arg("capacity"), py::arg("routes"))
        .def("delta", &MoveEvaluator::delta, py::arg("kind"), py::arg("a"), py::arg("ia"), py::arg("b"), py::arg("ib"))
        .def("apply", &MoveEvaluator::apply, py::arg("kind"), py::arg("a"), py::arg("ia"), py::arg("b"), py::arg("ib"))
        .def("scan", &MoveEvaluator::scan, py::arg("operators"), py::arg("strategy") = "first",
             py::arg("remaining_seconds") = py::none(), py::arg("neighbours") = py::none());
}
