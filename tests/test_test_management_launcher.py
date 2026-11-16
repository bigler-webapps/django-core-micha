import io

from django_core_micha.scripts import isolated_test_start


def test_launcher_reports_only_fixed_diagnostic_without_process_output(monkeypatch, tmp_path, capsys):
    class FailedProcess:
        stdout = io.StringIO("synthetic-private-value permission denied\n")
        returncode = 1

        def poll(self):
            return self.returncode

    monkeypatch.setattr(isolated_test_start.subprocess, "Popen", lambda *args, **kwargs: FailedProcess())
    assert isolated_test_start._run_private(["synthetic"], cwd=tmp_path, env={}) == 1
    output = capsys.readouterr().out
    assert "Docker denied access" in output
    assert "synthetic-private-value" not in output
