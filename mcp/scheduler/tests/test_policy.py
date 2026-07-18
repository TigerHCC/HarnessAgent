import policy

def test_token_roundtrips_for_same_action_and_args():
    args = {"name": "x", "kind": "cron", "expr": "0 9 * * *"}
    tok = policy.make_token("sched_create", args)
    assert policy.verify_token("sched_create", args, tok, now=100.0, issued_at=100.0)

def test_token_rejected_when_args_differ():
    tok = policy.make_token("sched_create", {"name": "x"})
    assert not policy.verify_token("sched_create", {"name": "y"}, tok, now=100.0, issued_at=100.0)

def test_token_expires_after_ttl():
    args = {"name": "x"}
    tok = policy.make_token("sched_delete", args)
    late = 100.0 + policy.TOKEN_TTL_SECONDS + 1
    assert not policy.verify_token("sched_delete", args, tok, now=late, issued_at=100.0)

def test_mutating_set_matches_spec():
    assert policy.MUTATING == {"sched_create", "sched_update", "sched_delete",
                               "sched_pause", "sched_resume", "sched_run_now"}
