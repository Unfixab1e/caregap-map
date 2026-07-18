"""The .env loader and the LLM cost-estimate helper."""

import os

from caregap_map.config import LlmConfig, load_env_file
from caregap_map.llm_extraction import estimate_cost_usd


class TestLoadEnvFile:
    def test_loads_values_without_overriding_environment(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text(
            "# comment\n"
            "\n"
            "OPENAI_API_KEY=sk-from-file\n"
            'CAREGAP_TEST_QUOTED="quoted value"\n'
            "CAREGAP_TEST_ALREADY_SET=file-value\n"
            "not a key value line\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("CAREGAP_TEST_QUOTED", raising=False)
        monkeypatch.setenv("CAREGAP_TEST_ALREADY_SET", "process-value")

        loaded = load_env_file(env)

        assert loaded == 2
        assert os.environ["OPENAI_API_KEY"] == "sk-from-file"
        assert os.environ["CAREGAP_TEST_QUOTED"] == "quoted value"
        # The process environment always wins over the file.
        assert os.environ["CAREGAP_TEST_ALREADY_SET"] == "process-value"

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("CAREGAP_TEST_QUOTED", raising=False)

    def test_missing_file_is_fine(self, tmp_path):
        assert load_env_file(tmp_path / "does-not-exist.env") == 0


class TestCostEstimate:
    def test_matches_configured_prices(self):
        config = LlmConfig(input_cost_per_mtok=0.15, output_cost_per_mtok=0.60)
        # 1M input + 1M output at the configured prices.
        assert estimate_cost_usd(1_000_000, 1_000_000, config) == 0.75

    def test_zero_tokens_zero_cost(self):
        assert estimate_cost_usd(0, 0, LlmConfig()) == 0.0
