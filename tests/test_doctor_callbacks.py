"""GUI structured callbacks get the same redaction as the CLI, without human-log parsing."""

from tutor import doctor
from tutor.doctor_rows import Row


def test_callback_output_redacts_credentials_and_can_suppress_stdout(tmp_path, monkeypatch, capsys):
    secret = "sk-test-not-a-real-key"
    monkeypatch.setenv("DOCTOR_TOKEN", secret)
    monkeypatch.setattr(doctor, "tool_python", lambda: [
        Row("FAIL", "synthetic " + secret, "retry")])
    rows, lines = [], []
    assert doctor.main([str(tmp_path)], output=lines.append, on_row=rows.append) == 1
    assert all(secret not in row.text for row in rows)
    assert secret not in "\n".join(lines) and not capsys.readouterr().out
