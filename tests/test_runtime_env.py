"""runtime_env: which variables count as credentials, and environments without them."""

from tutor.runtime_env import key_names, secret_name, without_credentials

FAKE = "sk-test-not-a-real-key"  # plainly fake: a secret scanner must not take it for real


def test_names_that_are_credentials_and_names_that_only_look_alike():
    for name in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "GITHUB_TOKEN", "db_password",
                 "AWS_SECRET_ACCESS_KEY", "OPENAI_APIKEY", "AUTHORIZATION"):
        assert secret_name(name), name
    # A whole part of the name only: these hold no credential, and Spark, Hadoop and the
    # course's tools need the first four.
    for name in ("JAVA_HOME", "HADOOP_HOME", "PATH", "WORKSPACE_DIR", "MONKEY", "KEYBOARD"):
        assert not secret_name(name), name


def test_without_credentials_keeps_everything_else():
    env = {"PATH": "/bin", "JAVA_HOME": "/java", "DEEPSEEK_API_KEY": FAKE, "Odd_Name": FAKE}

    # Named in `also` in another case, as Windows compares names.
    assert without_credentials(env, also=["ODD_NAME"]) == {"PATH": "/bin", "JAVA_HOME": "/java"}
    assert without_credentials(env) == {"PATH": "/bin", "JAVA_HOME": "/java", "Odd_Name": FAKE}


def test_key_names_come_from_the_course_and_from_the_shared_file(tmp_path):
    course = tmp_path / "courses" / "demo"
    exercise = course / "batches" / "b1" / "exercises" / "e1"
    exercise.mkdir(parents=True)
    (course / "workspace.toml").write_text(
        'course = "demo"\n[llm.profiles.own]\nprovider = "deepseek"\nmodel = "m"\n'
        'api_key_env = "COURSE_VARIABLE"\n', encoding="utf-8")
    (course.parent / "llm.toml").write_text(
        '[providers.card]\nkind = "openai"\napi_key_env = "CARD_VARIABLE"\n'
        '[profiles.shared]\nprovider = "card"\nmodel = "m"\napi_key_env = "PROFILE_VARIABLE"\n',
        encoding="utf-8")

    found = key_names([exercise / "test_student.py"])

    assert found == {"COURSE_VARIABLE", "CARD_VARIABLE", "PROFILE_VARIABLE"}


def test_key_names_outside_a_course_or_with_a_broken_file_are_empty(tmp_path):
    assert key_names([tmp_path / "test_student.py"]) == frozenset()
    (tmp_path / "workspace.toml").write_text("not [toml", encoding="utf-8")
    assert key_names([tmp_path / "test_student.py"]) == frozenset()
