def validate_questions(questions: list[dict]) -> list[dict]:
    """Run validation checks on a list of questions, adding flags.

    Each question dict gets a 'flags' list added/modified.
    Returns the list of questions (mutated in place).
    """
    for q in questions:
        q["flags"] = q.get("flags", [])
        num = q.get("number", "")
        text = q.get("text", "")
        marks = q.get("marks")

        # Check text presence
        if not text and not q.get("subs"):
            q["flags"].append({"level": "review", "code": "NO_TEXT", "reason": f"Question {num} has no text or subquestions."})

        # Check marks — skip if any sub is an OR alternative (not additive)
        subs = q.get("subs") or []
        if subs and marks is not None:
            has_or = any(s.get("is_or_alternative") for s in subs)
            if not has_or:
                sub_sum = sum(s.get("marks", 0) or 0 for s in subs)
                if sub_sum > marks:
                    q["flags"].append(
                        {"level": "review", "code": "SUBS_MARKS_EXCEED", "reason": f"Subquestion marks sum ({sub_sum}) > question marks ({marks})."}
                    )

        # Check options for MCQ
        if q.get("options"):
            if len(q["options"]) < 2:
                q["flags"].append({"level": "review", "code": "FEW_OPTIONS", "reason": f"MCQ {num} has only {len(q['options'])} options."})
            empties = sum(1 for o in q["options"] if not str(o).strip())
            if empties:
                q["flags"].append({"level": "review", "code": "EMPTY_OPTION", "reason": f"MCQ {num} has {empties} empty option(s). The OCR could not read them."})
        # Check continuation flags
        if q.get("incomplete") and not q.get("continue_next"):
            q["flags"].append({"level": "review", "code": "INCOMPLETE", "reason": f"Question {num} marked incomplete but no continue_next."})

        # Check low confidence
        if q.get("conf", 1.0) < 0.6:
            q["flags"].append({"level": "review", "code": "LOW_CONFIDENCE", "reason": f"Question {num} confidence {q.get('conf', 0):.2f}."})

    # Numbering continuity check
    _check_numbering(questions)

    # Options consistency: flag questions with options in parts where options are uncommon
    _check_options_consistency(questions)

    return questions


def _check_options_consistency(questions: list[dict]) -> None:
    by_part: dict[str, list[dict]] = {}
    for q in questions:
        by_part.setdefault(str(q.get("part", "?")).upper(), []).append(q)

    for part, qs in by_part.items():
        if len(qs) < 3:
            continue
        with_opts = sum(1 for q in qs if q.get("options"))
        no_opts = len(qs) - with_opts
        # if a question has options but most questions in this part do not, flag it
        for q in qs:
            if q.get("options") and no_opts > with_opts:
                q["flags"].append(
                    {
                        "level": "review",
                        "code": "UNEXPECTED_OPTIONS",
                        "reason": f"Question {q.get('number')} in part {part} has answer options, but most questions in this part are non-MCQ.",
                    }
                )


def _check_numbering(questions: list[dict]) -> None:
    parts = {}
    for q in questions:
        part = q.get("part", "?")
        parts.setdefault(part, []).append(q)

    for part, qs in parts.items():
        nums = []
        for q in qs:
            try:
                nums.append(int(str(q.get("number", "0")).split(".")[0]))
            except ValueError:
                nums.append(0)
        for i in range(1, len(nums)):
            if nums[i] <= nums[i - 1] and nums[i] != 0:
                qs[i]["flags"].append(
                    {"level": "review", "code": "NUM_SEQUENCE", "reason": f"Question numbers not increasing: {qs[i-1].get('number')} -> {qs[i].get('number')} in part {part}."}
                )


_media_flag_note = "media association pending"


def compute_confidence(question: dict) -> dict:
    """Compute an overall confidence dict from the question's signals."""
    flags = question.get("flags", [])
    conf = question.get("conf", 0.95)

    penalty = 0.0
    for f in flags:
        level = f.get("level", "review")
        if level == "error":
            penalty += 0.3
        elif level == "review":
            penalty += 0.15
        elif level == "info":
            penalty += 0.05

    ocr = max(0.0, conf)
    qseg = max(0.0, 1.0 - penalty)
    overall = round((ocr * 0.5 + qseg * 0.5), 3)

    return {
        "ocr": round(ocr, 3),
        "question_segmentation": round(qseg, 3),
        "overall": overall,
        "flag_count": len(flags),
    }


def check_document_part_counts(questions: list[dict], structure: dict) -> list[dict]:
    """Compare extracted question counts per part against expected section counts.

    Returns a list of warning dicts.
    """
    expected_counts = structure.get("counts", {})
    if not expected_counts:
        return []

    actual: dict[str, int] = {}
    for q in questions:
        part = str(q.get("part", "?")).upper()
        actual[part] = actual.get(part, 0) + 1

    warnings = []
    for part, expected in expected_counts.items():
        got = actual.get(part, 0)
        if got < expected * 0.7:
            warnings.append(
                {
                    "level": "review",
                    "code": "LOW_PART_COUNT",
                    "reason": f"Part {part} has {got} extracted questions but the section header indicates ~{expected}. Missing questions may exist on some pages.",
                }
            )
    return warnings