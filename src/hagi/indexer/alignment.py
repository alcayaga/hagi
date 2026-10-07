"""Sentence timestamp offset estimation and banded DP subtitle alignment."""

import bisect
import difflib

REFRESH_THRESHOLD_SECONDS = 2.0


def estimate_timestamp_offset(
    existing_sentences: list[dict],
    new_sentences: list[dict],
    max_shift: float = 30.0,
) -> float:
    """Estimate a global constant timestamp offset between existing and new sentences.

    Compares exact or near-identical text matches to determine if a uniform time shift exists
    between two different media releases (e.g., due to trimmed or added sponsor cards).

    Args:
        existing_sentences (list[dict]): Chronologically ordered list of existing sentence dicts.
        new_sentences (list[dict]): Chronologically ordered list of new sentence dicts.
        max_shift (float): Maximum plausible time shift in seconds to consider. Defaults to 30.0.

    Returns:
        float: The detected median timestamp offset (new_start - old_start), or 0.0 if negligible.
    """
    if not existing_sentences or not new_sentences:
        return 0.0

    ex_lookup = {}
    for ex in existing_sentences:
        t = ex.get("text", "").strip()
        if len(t) >= 8 and "start_time" in ex and ex["start_time"] is not None:
            ex_lookup.setdefault(t, []).append(ex["start_time"])

    candidate_diffs = []
    for new_s in new_sentences:
        t = new_s["text"].strip()
        if t in ex_lookup:
            for old_start in ex_lookup[t]:
                diff = new_s["start_time"] - old_start
                if abs(diff) <= max_shift:
                    candidate_diffs.append(diff)

    min_required = min(3, len(existing_sentences))
    if not candidate_diffs or len(candidate_diffs) < min_required:
        return 0.0

    candidate_diffs.sort()
    median_diff = candidate_diffs[len(candidate_diffs) // 2]

    consensus_count = sum(1 for d in candidate_diffs if abs(d - median_diff) <= 0.5)
    if consensus_count >= min_required and (consensus_count / len(candidate_diffs)) >= 0.4:
        if abs(median_diff) > 0.3:
            return round(median_diff, 3)

    return 0.0


def align_and_update_sentences(
    conn,
    media_id: int,
    language: str,
    new_sentences: list[dict],
    global_offset: float = 0.0,
) -> tuple[int, int, int]:
    """Align new sentences against existing sentences for a specific media_id and language.

    Uses a banded dynamic programming alignment algorithm with beam pruning to preserve
    existing sentence IDs across retimed, split, or merged lines.

    Args:
        conn: Database connection.
        media_id (int): Target media ID.
        language (str): Subtitle language code (e.g. 'jpn', 'eng', 'spa').
        new_sentences (list[dict]): List of new sentence dicts with 'start_time', 'end_time', 'text'.
        global_offset (float): Pre-estimated constant time offset between releases. Defaults to 0.0.

    Returns:
        tuple[int, int, int]: (updates_count, inserts_count, deletes_count) or (-1, -1, -1) on failure.
    """
    query = (
        "SELECT id, language, start_time, end_time, text FROM sentences "
        "WHERE media_id = ? AND language = ? ORDER BY start_time, id"
    )
    existing = conn.execute(query, (media_id, language)).fetchall()
    existing_list = [dict(row) for row in existing]

    N = len(new_sentences)
    M = len(existing_list)

    if M == 0:
        inserts = [(media_id, language, s["start_time"], s["end_time"], s["text"]) for s in new_sentences]
        if inserts:
            conn.executemany(
                "INSERT INTO sentences (media_id, language, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)",
                inserts,
            )
        return (0, len(inserts), 0)

    if N == 0:
        deletes = [(ex["id"],) for ex in existing_list]
        if deletes:
            conn.executemany("DELETE FROM sentences WHERE id=?", deletes)
        return (0, 0, len(deletes))

    adjusted_existing_times = [ex["start_time"] + global_offset for ex in existing_list]

    prev_dp = {}
    curr_dp = {0: (0.0, 0.0, 0.0)}

    back_ptr = [{} for _ in range(N + 1)]

    C_ins = (1.0, 0.0, 0.0)
    C_del = (1.0, 0.0, 0.0)

    for i in range(N + 1):
        if i > 0:
            prev_dp = curr_dp
            curr_dp = {}

        if i == 0:
            start_j, end_j = 0, M
        else:
            new_time = new_sentences[i - 1]["start_time"]
            start_j = bisect.bisect_left(adjusted_existing_times, new_time - 15.0)
            end_j = bisect.bisect_right(adjusted_existing_times, new_time + 15.0)
            if i > 1:
                prev_time = new_sentences[i - 2]["start_time"]
                start_j = min(start_j, bisect.bisect_left(adjusted_existing_times, prev_time - 15.0))
                end_j = max(end_j, bisect.bisect_right(adjusted_existing_times, prev_time + 15.0))

        running_min_norm = (float("inf"), float("inf"), float("inf"))
        running_min_k = None

        running_min_norm_ins = (float("inf"), float("inf"), float("inf"))
        running_min_k_ins = None

        for k, p_cost in prev_dp.items():
            norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1], p_cost[2] - k * C_del[2])
            if k <= start_j - 1:
                if norm < running_min_norm:
                    running_min_norm = norm
                    running_min_k = k
                if norm < running_min_norm_ins:
                    running_min_norm_ins = norm
                    running_min_k_ins = k

        curr_backs = {}

        for j in range(start_j, end_j + 1):
            if i == 0 and j == 0:
                continue

            best_cost = (float("inf"), float("inf"), float("inf"))
            best_back = None

            if i > 0:
                if j in prev_dp:
                    k = j
                    p_cost = prev_dp[k]
                    norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1], p_cost[2] - k * C_del[2])
                    if norm < running_min_norm_ins:
                        running_min_norm_ins = norm
                        running_min_k_ins = k

                if running_min_k_ins is not None:
                    ins_cost = (
                        running_min_norm_ins[0] + j * C_del[0] + C_ins[0],
                        running_min_norm_ins[1] + j * C_del[1] + C_ins[1],
                        running_min_norm_ins[2] + j * C_del[2] + C_ins[2],
                    )
                    if ins_cost < best_cost:
                        best_cost = ins_cost
                        best_back = (1, running_min_k_ins)

            if i > 0 and j > 0:
                if (j - 1) in prev_dp:
                    k = j - 1
                    p_cost = prev_dp[k]
                    norm = (p_cost[0] - k * C_del[0], p_cost[1] - k * C_del[1], p_cost[2] - k * C_del[2])
                    if norm < running_min_norm:
                        running_min_norm = norm
                        running_min_k = k

                ex = existing_list[j - 1]
                new_s = new_sentences[i - 1]
                dist_start = abs((ex["start_time"] + global_offset) - new_s["start_time"])

                if dist_start <= REFRESH_THRESHOLD_SECONDS:
                    dist_end = min(abs((ex["end_time"] + global_offset) - new_s["end_time"]), REFRESH_THRESHOLD_SECONDS)

                    text_ratio = difflib.SequenceMatcher(None, ex["text"], new_s["text"]).ratio()
                    text_penalty = 1.0 - text_ratio

                    if running_min_k is not None:
                        best_k_cost = (
                            running_min_norm[0] + (j - 1) * C_del[0],
                            running_min_norm[1] + (j - 1) * C_del[1],
                            running_min_norm[2] + (j - 1) * C_del[2],
                        )
                        best_k = running_min_k

                        match_cost = (
                            best_k_cost[0],
                            best_k_cost[1] + dist_start + dist_end * 0.1,
                            best_k_cost[2] + text_penalty,
                        )

                        if match_cost < best_cost:
                            best_cost = match_cost
                            best_back = (0, best_k)

            if j > 0 and (j - 1) in curr_dp:
                prev_cost = curr_dp[j - 1]
                del_cost = (prev_cost[0] + C_del[0], prev_cost[1] + C_del[1], prev_cost[2] + C_del[2])
                if del_cost < best_cost:
                    best_cost = del_cost
                    prev_back = curr_backs.get(j - 1)
                    if isinstance(prev_back, tuple) and prev_back[0] == 2:
                        best_back = prev_back
                    else:
                        best_back = (2, j - 1, prev_back)

            if best_cost[0] != float("inf"):
                curr_dp[j] = best_cost
                curr_backs[j] = best_back

        if len(curr_dp) > 300:
            def pruning_key_high(item):
                """Pruning key prioritizing highest progress states."""
                j_idx, cost = item
                return (cost[0] - j_idx * C_del[0], cost[1] - j_idx * C_del[1], cost[2] - j_idx * C_del[2], -j_idx)

            def pruning_key_low(item):
                """Pruning key prioritizing lower progress states."""
                j_idx, cost = item
                return (cost[0] - j_idx * C_del[0], cost[1] - j_idx * C_del[1], cost[2] - j_idx * C_del[2], j_idx)

            best_items = dict(sorted(curr_dp.items(), key=pruning_key_high)[:150])
            best_items.update(dict(sorted(curr_dp.items(), key=pruning_key_low)[:150]))

            if 0 in curr_dp and 0 not in best_items:
                best_items[0] = curr_dp[0]

            curr_dp = dict(best_items)

        back_ptr[i] = {k: curr_backs[k] for k in curr_dp if k in curr_backs}

    if M > 0 and not curr_dp:
        print(f"Alignment notice: Could not align subtitle sentences for language {language}.")
        return (-1, -1, -1)

    if M not in back_ptr[N]:
        j = max(curr_dp.keys()) if curr_dp else 0
        i = N
    else:
        i, j = N, M

    matches = {}

    while i > 0 or j > 0:
        if j not in back_ptr[i]:
            if i > 0:
                i -= 1
            else:
                j -= 1
            continue

        b = back_ptr[i][j]
        if isinstance(b, tuple):
            if b[0] == 0:
                matches[i - 1] = j - 1
                i -= 1
                j = b[1]
            elif b[0] == 1:
                i -= 1
                j = b[1]
            elif b[0] == 2:
                origin_j, prev_back = b[1], b[2]
                if isinstance(prev_back, tuple):
                    if prev_back[0] == 0:
                        matches[i - 1] = origin_j - 1
                        i -= 1
                        j = prev_back[1]
                    elif prev_back[0] == 1:
                        i -= 1
                        j = prev_back[1]
                    else:
                        j -= 1
                else:
                    j -= 1
        else:
            j -= 1

    updates = []
    inserts = []
    matched_existing_indices = set(matches.values())

    for idx, new_s in enumerate(new_sentences):
        if idx in matches:
            ex = existing_list[matches[idx]]
            updates.append((language, new_s["start_time"], new_s["end_time"], new_s["text"], ex["id"]))
        else:
            inserts.append((media_id, language, new_s["start_time"], new_s["end_time"], new_s["text"]))

    deletes = [(existing_list[k]["id"],) for k in range(M) if k not in matched_existing_indices]

    if updates:
        conn.executemany("UPDATE sentences SET language=?, start_time=?, end_time=?, text=? WHERE id=?", updates)
    if inserts:
        conn.executemany(
            "INSERT INTO sentences (media_id, language, start_time, end_time, text) VALUES (?, ?, ?, ?, ?)",
            inserts,
        )
    if deletes:
        conn.executemany("DELETE FROM sentences WHERE id=?", deletes)

    return (len(updates), len(inserts), len(deletes))
