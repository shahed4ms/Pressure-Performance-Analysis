
import csv
import html
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BAT_MIN_PRESSURE = 100
BOWL_MIN_PRESSURE = 90
MIN_PRESSURE_MATCHES = 8
BAT_MIN_BASELINE = 100
BOWL_MIN_BASELINE = 80
WICKET_VALUE = 20.0
PRESSURE_CUTOFF = 0.35


def read_csv(name):
    with (ROOT / name).open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def integer(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def scheduled_balls(value):
    if not value:
        return 120
    ball_count = integer(value)
    return ball_count if ball_count > 0 else 120


def clamp(value, lower=0.0, upper=1.0):
    return max(lower, min(upper, value))


def dismissal_is_bowler_credit(delivery):
    return delivery["dismissal_type"] in {
        "Bowled",
        "Caught",
        "LBW",
        "Stump",
        "Hit Wicket",
    }


def delivery_total_runs(delivery):
    # The supplied extras1_runs field is a category code, not an actual run count.
    # Use the batter runs plus the one-run base associated with an extras event.
    extras = delivery["extras_type"]
    extra_run = int(extras in {"wide", "Overstep", "byes", "legbyes", "other"})
    return integer(delivery["runs"]) + extra_run


def calculate_ratings(tagged, player_kind, cutoff=PRESSURE_CUTOFF, wicket_value=WICKET_VALUE):
    stats = defaultdict(lambda: {
        "name": "",
        "pressure": defaultdict(lambda: [0.0, 0.0, 0.0]),
        "normal": defaultdict(lambda: [0.0, 0.0, 0.0]),
        "match_pressure": defaultdict(lambda: [0.0, 0.0, 0.0]),
        "matches": set(),
    })

    player_names = {}
    for row in tagged.values():
        player_names[row["striker_id"]] = row["striker_name"]
        player_names[row["non_striker_id"]] = row["non_striker_name"]

    for row in tagged.values():
        pressure = row["_pressure"] >= cutoff
        fmt = row["_format"]
        match_id = row["_match_id"]
        if player_kind == "batter":
            bucket = "pressure" if pressure else "normal"
            player_id = row["striker_id"]
            if row["extras_type"] != "wide":
                item = stats[player_id]
                item["name"] = row["striker_name"]
                item[bucket][fmt][0] += 1
                item[bucket][fmt][1] += row["_runs"]
                item[bucket][fmt][2] += int(
                    row["_wicket"] and row["dismissed_player_id"] == player_id
                )
                if pressure:
                    item["matches"].add(match_id)
                    match_bucket = item["match_pressure"][(fmt, match_id)]
                    match_bucket[0] += 1
                    match_bucket[1] += row["_runs"]
                    match_bucket[2] += int(row["_wicket"] and row["dismissed_player_id"] == player_id)
            dismissed_id = row["dismissed_player_id"]
            if row["_wicket"] and dismissed_id and dismissed_id != player_id:
                item = stats[dismissed_id]
                item["name"] = player_names.get(dismissed_id, dismissed_id)
                item[bucket][fmt][2] += 1
                if pressure:
                    item["matches"].add(match_id)
                    item["match_pressure"][(fmt, match_id)][2] += 1
            continue
        else:
            player_id, name = row["bowler_id"], row["bowler_name"]
            opportunity = row["_legal"]
            if not opportunity:
                continue
            runs = row["_runs_conceded"]
            out = int(row["_wicket"] and dismissal_is_bowler_credit(row))

        bucket = "pressure" if pressure else "normal"
        item = stats[player_id]
        item["name"] = name
        item[bucket][fmt][0] += 1
        item[bucket][fmt][1] += runs
        item[bucket][fmt][2] += out
        if pressure:
            item["matches"].add(match_id)
            match_bucket = item["match_pressure"][(fmt, match_id)]
            match_bucket[0] += 1
            match_bucket[1] += runs
            match_bucket[2] += out

    results = []
    for player_id, item in stats.items():
        formats = set(item["pressure"]) | set(item["normal"])
        pressure_n = baseline_n = pressure_runs = normal_runs = 0.0
        pressure_outs = normal_outs = 0.0
        format_effects = []
        for fmt in formats:
            p_n, p_runs, p_outs = item["pressure"][fmt]
            n_n, n_runs, n_outs = item["normal"][fmt]
            if p_n <= 0 or n_n <= 0:
                continue
            pressure_n += p_n
            baseline_n += n_n
            pressure_runs += p_runs
            normal_runs += n_runs
            pressure_outs += p_outs
            normal_outs += n_outs
            if player_kind == "batter":
                effect = 100.0 * (
                    (p_runs / p_n - n_runs / n_n)
                    - wicket_value * (p_outs / p_n - n_outs / n_n)
                )
            else:
                effect = 100.0 * (
                    (n_runs / n_n - p_runs / p_n)
                    + wicket_value * (p_outs / p_n - n_outs / n_n)
                )
            format_effects.append((fmt, p_n, effect))

        if not format_effects:
            continue
        pressure_matches = len(item["matches"])
        min_pressure = BAT_MIN_PRESSURE if player_kind == "batter" else BOWL_MIN_PRESSURE
        min_baseline = BAT_MIN_BASELINE if player_kind == "batter" else BOWL_MIN_BASELINE
        if (
            pressure_n < min_pressure
            or baseline_n < min_baseline
            or pressure_matches < MIN_PRESSURE_MATCHES
        ):
            continue
        effect = sum(n * value for _, n, value in format_effects) / sum(
            n for _, n, _ in format_effects
        )

        match_effects = []
        positive_matches = 0
        eligible_match_count = 0
        for fmt, match_id in item["match_pressure"]:
            m_n, m_runs, m_outs = item["match_pressure"][(fmt, match_id)]
            if m_n < 5:
                continue
            n_n, n_runs, n_outs = item["normal"][fmt]
            if n_n <= 0:
                continue
            if player_kind == "batter":
                match_effect = 100.0 * (
                    (m_runs / m_n - n_runs / n_n)
                    - wicket_value * (m_outs / m_n - n_outs / n_n)
                )
            else:
                match_effect = 100.0 * (
                    (n_runs / n_n - m_runs / m_n)
                    + wicket_value * (m_outs / m_n - n_outs / n_n)
                )
            match_effects.append(match_effect)
            positive_matches += int(match_effect > 0)
            eligible_match_count += 1
        se = (
            statistics.stdev(match_effects) / math.sqrt(len(match_effects))
            if len(match_effects) > 1
            else abs(effect)
        )
        reliability = pressure_n / (pressure_n + 100.0)
        score = effect * reliability - 0.5 * se
        results.append({
            "player_id": player_id,
            "player": item["name"],
            "score": score,
            "effect": effect,
            "pressure_opportunities": int(pressure_n),
            "baseline_opportunities": int(baseline_n),
            "pressure_matches": pressure_matches,
            "matches_with_5plus_pressure_opportunities": eligible_match_count,
            "positive_pressure_match_share": (
                positive_matches / eligible_match_count if eligible_match_count else 0.0
            ),
            "pressure_rate": pressure_runs / pressure_n,
            "baseline_rate": normal_runs / baseline_n,
            "pressure_out_rate": pressure_outs / pressure_n,
            "baseline_out_rate": normal_outs / baseline_n,
            "formats": ", ".join(sorted(fmt for fmt, _, _ in format_effects)),
        })
    return sorted(results, key=lambda row: (-row["score"], -row["effect"], row["player"]))


def markdown_table(rows, cols):
    lines = [
        "| " + " | ".join(title for title, _ in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value(row)) for _, value in cols) + " |")
    return "\n".join(lines)


def markdown_to_html(markdown):
    lines = markdown.splitlines()
    rendered = []
    index = 0

    def inline(text):
        escaped = html.escape(text)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading = re.match(r"^(#{1,6}) (.*)$", line)
        if heading:
            level = len(heading.group(1))
            rendered.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if line.startswith("|"):
            table_rows = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    table_rows.append(cells)
                index += 1
            if table_rows:
                header = "".join(f"<th>{inline(cell)}</th>" for cell in table_rows[0])
                body = "".join(
                    "<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>"
                    for row in table_rows[1:]
                )
                rendered.append(f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>")
            continue
        if line.startswith("- "):
            items = []
            while index < len(lines) and lines[index].startswith("- "):
                items.append(f"<li>{inline(lines[index][2:])}</li>")
                index += 1
            rendered.append("<ul>" + "".join(items) + "</ul>")
            continue
        paragraph = [line.strip()]
        index += 1
        while (
            index < len(lines)
            and lines[index].strip()
            and not lines[index].startswith(("#", "|", "- "))
        ):
            paragraph.append(lines[index].strip())
            index += 1
        rendered.append(f"<p>{inline(' '.join(paragraph))}</p>")

    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Pressure performance methodology</title>
<style>
body { font-family: Arial, sans-serif; font-size: 10pt; line-height: 1.4; color: #202124; margin: 36pt; }
h1 { color: #15395b; font-size: 22pt; margin-bottom: 18pt; }
h2 { color: #15395b; font-size: 15pt; margin-top: 20pt; border-bottom: 1px solid #c9d5e2; padding-bottom: 4pt; }
p, li { margin: 5pt 0; }
table { border-collapse: collapse; width: 100%; margin: 9pt 0 15pt; font-size: 9pt; }
th { background: #eaf0f6; text-align: left; }
th, td { border: 1px solid #b8c2cc; padding: 5pt; }
code { font-family: Menlo, monospace; font-size: 9pt; }
</style></head><body>""" + "".join(rendered) + "</body></html>"


def build_tagged_deliveries():
    competition_rows = read_csv("competitions.csv")
    competitions = {row["comp_id"]: row["match_format"] for row in competition_rows}
    matches = {row["match_id"]: row for row in read_csv("matches.csv")}
    raw_deliveries = read_csv("ball_by_ball.csv")
    grouped = defaultdict(list)
    for index, row in enumerate(raw_deliveries):
        if row["match_id"] not in matches or row["innings"] not in {"1", "2"}:
            continue
        row["_index"] = index
        row["_runs"] = integer(row["runs"])
        row["_wicket"] = row["is_wicket"].lower() == "true"
        row["_legal"] = row["extras_type"] not in {"wide", "Overstep"}
        row["_runs_conceded"] = row["_runs"] + int(
            row["extras_type"] in {"wide", "Overstep"}
        )
        grouped[(row["match_id"], row["innings"])].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: (integer(row["innings_ball_num"]), row["_index"]))

    tagged = {}
    pressure_delivery_count = 0
    chase_count = 0
    for match_id, match in matches.items():
        first = grouped.get((match_id, "1"), [])
        second = grouped.get((match_id, "2"), [])
        if not first or not second:
            continue
        chase_count += 1
        target = integer(match["revised_target"])
        if target <= 0:
            target = sum(delivery_total_runs(row) for row in first) + 1
        max_balls = scheduled_balls(match["second_inn_revised_overs"])
        if max_balls == 120 and match["first_inn_revised_overs"]:
            max_balls = scheduled_balls(match["first_inn_revised_overs"])
        runs_before = wickets_before = legal_before = 0
        match_format = competitions.get(match["comp_id"], "unknown")
        for row in second:
            balls_left = max(0, max_balls - legal_before)
            runs_needed = max(0, target - runs_before)
            required_rate = runs_needed * 6 / max(1, balls_left)
            pressure = (
                0.65 * clamp((required_rate - 8.0) / 10.0)
                + 0.25 * wickets_before / 9.0
                + 0.10 * (1.0 - clamp(legal_before / max(1, max_balls)))
            )
            row["_pressure"] = (
                pressure if runs_needed > 0 and balls_left > 0 else 0.0
            )
            row["_format"] = match_format
            row["_match_id"] = match_id
            tagged[(match_id, row["_index"])] = row
            pressure_delivery_count += int(row["_pressure"] >= PRESSURE_CUTOFF)
            runs_before += delivery_total_runs(row)
            wickets_before += int(row["_wicket"])
            legal_before += int(row["_legal"])
    return tagged, matches, raw_deliveries, chase_count, pressure_delivery_count


def rank_correlation(train_results, test_results):
    train_by_id = {row["player_id"]: row for row in train_results}
    test_by_id = {row["player_id"]: row for row in test_results}
    common = set(train_by_id) & set(test_by_id)
    if len(common) < 2:
        return len(common), None, None
    train_order = sorted(common, key=lambda pid: -train_by_id[pid]["score"])
    test_order = sorted(common, key=lambda pid: -test_by_id[pid]["score"])
    train_rank = {player_id: index for index, player_id in enumerate(train_order)}
    test_rank = {player_id: index for index, player_id in enumerate(test_order)}
    train_mean = test_mean = (len(common) - 1) / 2
    numerator = sum(
        (train_rank[player_id] - train_mean) * (test_rank[player_id] - test_mean)
        for player_id in common
    )
    denominator = math.sqrt(
        sum((train_rank[player_id] - train_mean) ** 2 for player_id in common)
        * sum((test_rank[player_id] - test_mean) ** 2 for player_id in common)
    )
    spearman = numerator / denominator if denominator else None
    concordant = discordant = 0
    common_players = sorted(common)
    for offset, first_id in enumerate(common_players):
        for second_id in common_players[offset + 1:]:
            train_diff = train_by_id[first_id]["score"] - train_by_id[second_id]["score"]
            test_diff = test_by_id[first_id]["score"] - test_by_id[second_id]["score"]
            if train_diff * test_diff > 0:
                concordant += 1
            elif train_diff * test_diff < 0:
                discordant += 1
    comparable_pairs = concordant + discordant
    pairwise_accuracy = (
        concordant / comparable_pairs if comparable_pairs else None
    )
    return len(common), spearman, pairwise_accuracy, comparable_pairs


def evaluate_holdout(tagged, matches):
    train = {
        key: row for key, row in tagged.items()
        if matches[row["_match_id"]]["match_date"] < "2025-01-01"
    }
    test = {
        key: row for key, row in tagged.items()
        if matches[row["_match_id"]]["match_date"] >= "2025-01-01"
    }
    train_match_count = len({row["_match_id"] for row in train.values()})
    test_match_count = len({row["_match_id"] for row in test.values()})
    report_rows = []
    details = {}
    for role, player_kind in (("batters", "batter"), ("bowlers", "bowler")):
        train_results = calculate_ratings(train, player_kind)
        test_results = calculate_ratings(test, player_kind)
        test_by_id = {row["player_id"]: row for row in test_results}
        train_by_id = {row["player_id"]: row for row in train_results}
        common_count, spearman, pairwise, pair_count = rank_correlation(
            train_results, test_results
        )
        train_top = train_results[:10]
        test_top = test_results[:10]
        top_overlap = len(
            {row["player_id"] for row in train_top}
            & {row["player_id"] for row in test_top}
        )
        evaluated_top = [
            test_by_id[row["player_id"]]
            for row in train_top
            if row["player_id"] in test_by_id
        ]
        report_rows.append({
            "role": role,
            "train_matches": train_match_count,
            "test_matches": test_match_count,
            "train_eligible": len(train_results),
            "test_eligible": len(test_results),
            "eligible_both_periods": common_count,
            "spearman_common_players": spearman,
            "pairwise_order_concordance": pairwise,
            "comparable_player_pairs": pair_count,
            "top10_overlap": top_overlap,
            "train_top10_test_eligible": len(evaluated_top),
            "train_top10_mean_test_effect": (
                statistics.mean(row["effect"] for row in evaluated_top)
                if evaluated_top else None
            ),
            "train_top10_positive_test_effect_share": (
                sum(row["effect"] > 0 for row in evaluated_top) / len(evaluated_top)
                if evaluated_top else None
            ),
        })
        details[role] = {
            "train_results": train_results,
            "test_results": test_results,
        }
    return report_rows, details


def main():
    tagged, matches, raw_deliveries, chase_count, pressure_delivery_count = (
        build_tagged_deliveries()
    )

    batters = calculate_ratings(tagged, "batter")
    bowlers = calculate_ratings(tagged, "bowler")
    if len(batters) < 10 or len(bowlers) < 10:
        raise RuntimeError(
            f"Eligibility filters yielded only {len(batters)} batters and {len(bowlers)} bowlers."
        )
    sensitivity = []
    base_names = {
        "batters": {row["player"] for row in batters[:10]},
        "bowlers": {row["player"] for row in bowlers[:10]},
    }
    for label, cutoff, wicket_value in (
        ("Cutoff 0.30", 0.30, WICKET_VALUE),
        ("Cutoff 0.40", 0.40, WICKET_VALUE),
        ("Wicket value 15", PRESSURE_CUTOFF, 15.0),
        ("Wicket value 25", PRESSURE_CUTOFF, 25.0),
    ):
        alt_batters = calculate_ratings(tagged, "batter", cutoff, wicket_value)
        alt_bowlers = calculate_ratings(tagged, "bowler", cutoff, wicket_value)
        sensitivity.append((
            label,
            len(base_names["batters"] & {row["player"] for row in alt_batters[:10]}),
            len(base_names["bowlers"] & {row["player"] for row in alt_bowlers[:10]}),
        ))
    output_cols = [
        "rank", "player_id", "player", "score", "effect",
        "pressure_opportunities", "baseline_opportunities", "pressure_matches",
        "matches_with_5plus_pressure_opportunities",
        "positive_pressure_match_share", "pressure_rate", "baseline_rate",
        "pressure_out_rate", "baseline_out_rate", "formats",
    ]
    holdout_rows, holdout_details = evaluate_holdout(tagged, matches)
    with (ROOT / "pressure_holdout_validation.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        fields = list(holdout_rows[0])
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(holdout_rows)
    for role, details in holdout_details.items():
        for period, key in (
            ("train_2022_2024", "train_results"),
            ("holdout_2025_2026", "test_results"),
        ):
            with (ROOT / f"{role}_{period}_rankings.csv").open(
                "w", newline="", encoding="utf-8"
            ) as file:
                writer = csv.DictWriter(file, fieldnames=output_cols)
                writer.writeheader()
                for rank, row in enumerate(details[key], 1):
                    writer.writerow({"rank": rank, **row})

    for role, results in (("batters", batters), ("bowlers", bowlers)):
        with (ROOT / f"{role}_pressure_rankings.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=output_cols)
            writer.writeheader()
            for rank, row in enumerate(results, 1):
                writer.writerow({"rank": rank, **row})

    report = []
    report += [
        "# Pressure performance in IPL and T20I chases",
        "",
        "## Executive summary",
        "",
        f"This analysis covers {chase_count:,} matches with both first and second innings in the supplied files, "
        f"from {len(raw_deliveries):,} delivery rows. It identifies high-pressure chase deliveries from the "
        f"pre-delivery state, then compares each eligible player with their own lower-pressure second-innings "
        f"performance. The model flags {pressure_delivery_count:,} high-pressure deliveries at the primary cutoff.",
        "",
        "The rankings are an evidence-based measure of improvement over a player's own baseline, not a claim "
        "that a player is universally better than all others. Higher adjusted scores mean a larger, more "
        "repeatable improvement in pressure situations.",
        "",
        "## Pressure definition",
        "",
        "Only second-innings chases are scored. A delivery is high pressure when its continuous pressure index "
        "is at least 0.35. Before each delivery, the index is:",
        "",
        "- 65% required-rate stress: `clip((required runs per over - 8) / 10, 0, 1)`.",
        "- 25% wicket stress: wickets already lost divided by nine.",
        "- 10% innings-stage stress: fraction of the scheduled legal balls already used.",
        "",
        "Required rate uses the target, score, and legal balls remaining. The three signals combine urgency, "
        "remaining batting resources, and time; an easy chase with many wickets and balls left is ordinary, "
        "while a steep rate, lost wickets, or a late chase moves the index upward. This is measured before the "
        "delivery, avoiding look-ahead from its outcome. The 0.35 cutoff is used for classification; the index "
        "also increases smoothly above that threshold.",
        "",
        "Revised-innings limits in the match file are stored as legal-ball counts (for example, 90 means 15 "
        "overs); those counts are used directly. Matches without a revised limit use 120 balls.",
        "",
        "## Performance and ranking method",
        "",
        "For batters, a batting opportunity is a delivery faced other than a wide. The outcome is batter runs; "
        "dismissals are assigned to the dismissed player, including run-outs. A batter's pressure effect per "
        "100 opportunities is the pressure-minus-ordinary change in runs per opportunity, less 20 times the "
        "change in dismissal rate.",
        "",
        "For bowlers, only legal balls are opportunities. Runs conceded are batter runs plus one for a wide or "
        "no-ball; byes and leg-byes are not charged to the bowler. Bowler-credited wickets (bowled, caught, LBW, "
        "stumped, and hit wicket) are rewarded. A bowler's pressure effect per 100 legal balls is ordinary "
        "runs conceded minus pressure runs conceded, plus 20 times the pressure-minus-ordinary wicket rate.",
        "",
        "The 20-run wicket value is a transparent, fixed run-equivalent penalty/reward, not a claim that every "
        "wicket has identical match value. For each player and format, the comparison baseline is their own "
        "second-innings performance on deliveries below the 0.35 cutoff. Format-specific effects are pooled "
        "by pressure opportunities so IPL and T20I context is not silently treated as identical.",
        "",
        "Eligibility requires at least 100 pressure batting opportunities or 90 pressure legal balls for bowlers, "
        "at least 100 or 80 ordinary opportunities respectively, and pressure participation in at least eight "
        "matches. The reported `score` shrinks the raw effect by `pressure opportunities / (pressure "
        "opportunities + 100)` and subtracts half the standard error across matches with at least five pressure "
        "opportunities. This reduces the advantage of small, volatile samples. Positive-match share is the "
        "fraction of those eligible match-level comparisons that exceed the player's baseline.",
        "",
        "## Top 10 batters",
        "",
        markdown_table(
            batters[:10],
            [
                ("Rank", lambda r: batters.index(r) + 1),
                ("Player", lambda r: r["player"]),
                ("Adjusted score", lambda r: f'{r["score"]:.2f}'),
                ("Effect / 100 opps", lambda r: f'{r["effect"]:.2f}'),
                ("Pressure opps", lambda r: r["pressure_opportunities"]),
                ("Matches", lambda r: r["pressure_matches"]),
                ("Positive matches", lambda r: f'{r["positive_pressure_match_share"]:.0%}'),
            ],
        ),
        "",
        "## Top 10 bowlers",
        "",
        markdown_table(
            bowlers[:10],
            [
                ("Rank", lambda r: bowlers.index(r) + 1),
                ("Player", lambda r: r["player"]),
                ("Adjusted score", lambda r: f'{r["score"]:.2f}'),
                ("Effect / 100 legal balls", lambda r: f'{r["effect"]:.2f}'),
                ("Pressure legal balls", lambda r: r["pressure_opportunities"]),
                ("Matches", lambda r: r["pressure_matches"]),
                ("Positive matches", lambda r: f'{r["positive_pressure_match_share"]:.0%}'),
            ],
        ),
        "",
        "In the tables, effect is improvement over the player's own ordinary baseline; adjusted score additionally "
        "accounts for opportunity volume and match-to-match uncertainty. The CSV files contain full rates and "
        "the player IDs needed to reproduce the dropdown selection.",
        "",
        "## Sensitivity check",
        "",
        "I reranked players using pressure cutoffs of 0.30 and 0.40, and wicket values of 15 and 25 "
        "run-equivalents, keeping the eligibility rules unchanged. The table gives the number of players "
        "overlapping with the primary top 10:",
        "",
        markdown_table(
            sensitivity,
            [
                ("Alternative", lambda r: r[0]),
                ("Batter overlap", lambda r: f'{r[1]}/10'),
                ("Bowler overlap", lambda r: f'{r[2]}/10'),
            ],
        ),
        "",
        "## Chronological holdout check",
        "",
        "To answer the request to train and check the rankings, I used matches dated before 2025-01-01 as "
        "the training period and matches on or after that date as the holdout. The rating formula and pressure "
        "definition are statistical scoring rules, not a supervised machine-learning model. Players are ranked "
        "from the training period, then independently rescored on the holdout to test whether that ordering "
        "carries forward.",
        "",
        markdown_table(
            holdout_rows,
            [
                ("Role", lambda r: r["role"].title()),
                ("Train/test matches", lambda r: f'{r["train_matches"]}/{r["test_matches"]}'),
                ("Eligible train/test", lambda r: f'{r["train_eligible"]}/{r["test_eligible"]}'),
                ("Eligible in both", lambda r: r["eligible_both_periods"]),
                ("Spearman rank correlation", lambda r: (
                    f'{r["spearman_common_players"]:.2f}'
                    if r["spearman_common_players"] is not None else "N/A"
                )),
                ("Pairwise rank concordance", lambda r: (
                    f'{r["pairwise_order_concordance"]:.0%} '
                    f'({r["comparable_player_pairs"]} pairs)'
                    if r["pairwise_order_concordance"] is not None else "N/A"
                )),
                ("Top-10 overlap", lambda r: f'{r["top10_overlap"]} players'),
                ("Train top 10 test-eligible", lambda r: (
                    f'{r["train_top10_test_eligible"]}/10'
                )),
                ("Mean holdout effect of eligible train top 10", lambda r: (
                    f'{r["train_top10_mean_test_effect"]:.2f}'
                    if r["train_top10_mean_test_effect"] is not None else "N/A"
                )),
                ("Positive holdout effects", lambda r: (
                    f'{r["train_top10_positive_test_effect_share"]:.0%}'
                    if r["train_top10_positive_test_effect_share"] is not None else "N/A"
                )),
            ],
        ),
        "",
        "These results do not support claiming reliable prediction yet. Only four batters and twelve bowlers "
        "met the minimum eligibility rules in both periods; the batter holdout had only eight eligible players. "
        "The common-player rank correlation was negative for batters and weakly negative for bowlers, while "
        "two players overlapped between each training and holdout top 10. The train-top-10 holdout effects are "
        "based only on players who independently passed the holdout filters, so the counts are small.",
        "",
        "This is an exploratory backtest, not a pristine blind experiment: model thresholds and eligibility were "
        "chosen during analysis of the supplied full-period dataset. A stronger accuracy claim would require "
        "freezing the method in advance and evaluating on a future season, or using nested leave-one-season-out "
        "validation. The `spearman_common_players` value measures rank association only among players eligible "
        "in both windows; it is not delivery-level or match-win accuracy.",
        "",
        "## Checks, reliability, and limitations",
        "",
        "- The pressure state is calculated from pre-delivery score, wickets, and legal-ball count; innings 1 and "
        "super-over innings are excluded from player scoring.",
        "- Each player's baseline is calculated separately within competition format. Minimum volume and "
        "eight-match eligibility limit tiny samples; reliability shrinkage and match-level uncertainty further "
        "reduce unstable estimates.",
        "- The supplied `extras1_runs` values behave as category codes rather than actual extra-run amounts. "
        "To reconstruct chase totals, the analysis adds one base run for a non-`None` extra; target estimation "
        "can therefore be off when an extra delivery added more than one run. Bowling run rates add only "
        "wide/no-ball penalties, and exclude byes/leg-byes in line with standard attribution.",
        "- Pressure is a model choice, not an observed label. The weights and cutoff are interpretable but "
        "subjective; there is no verified win-probability model or ball-by-ball win probability in the supplied data.",
        "- This is an observational comparison, not a causal estimate. Batting order, team strength, opposition, "
        "venue, and role can influence the opportunity to face pressure. A player's own historical baseline may "
        "also be noisy, and the 20-run wicket value and extra-run reconstruction affect exact ordering.",
        "- Rankings should be treated as relative evidence within the supplied 2022-onward sample, not as "
        "career-wide or future-performance guarantees.",
        "",
        "## Reproduction",
        "",
        "Run `python3 pressure_analysis.py` in the folder containing the supplied CSVs. The script writes "
        "`batters_pressure_rankings.csv`, `bowlers_pressure_rankings.csv`, the chronological training and "
        "holdout rankings, `pressure_holdout_validation.csv`, and this report as Markdown and HTML. On macOS, "
        "create the editable Word document with `textutil -convert docx -output "
        "methodology_report.docx methodology_report.html`. All calculations use Python's standard library.",
        "",
    ]
    markdown_report = "\n".join(report)
    (ROOT / "methodology_report.md").write_text(markdown_report, encoding="utf-8")
    (ROOT / "methodology_report.html").write_text(
        markdown_to_html(markdown_report), encoding="utf-8"
    )
    print(f"Analyzed {chase_count} chases and {pressure_delivery_count} high-pressure deliveries.")
    for label, rows in (("BATTERS", batters[:10]), ("BOWLERS", bowlers[:10])):
        print(f"\n{label}")
        for rank, row in enumerate(rows, 1):
            print(
                f'{rank:2}. {row["player"]:<24} score={row["score"]:7.2f} '
                f'effect={row["effect"]:7.2f} n={row["pressure_opportunities"]:4} '
                f'matches={row["pressure_matches"]:2} positive={row["positive_pressure_match_share"]:.0%}'
            )


if __name__ == "__main__":
    main()
