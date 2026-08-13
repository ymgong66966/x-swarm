from __future__ import annotations

from xswarm.evals import harness


def test_fixtures_carry_everything_the_editor_needs():
    for fixture in harness.load_fixtures():
        assert len(fixture["grounded_claims"]) >= 3
        assert fixture["caveat"]
        assert fixture["unverified_claims"]


def test_hook_rejects_throat_clearing():
    assert harness.hook_ok("Speculative decoding is finally worth the complexity.")
    assert not harness.hook_ok("Interesting paper on speculative decoding.")


def test_dry_run_eval_scores_the_fallback_writer():
    report = harness.run_eval(dry_run=True)

    assert report.provider == "dry-run"
    assert len(report.scores) == 9
    assert 0.0 <= report.overall <= 100.0
    # No model means no visual alt text, so the Editor blocks everything by design.
    assert report.pass_rate == 0.0
    assert harness.format_report(report).startswith("provider=dry-run")
    assert report.to_dict()["drafts"][0]["slug"] == "speculative-decoding"
