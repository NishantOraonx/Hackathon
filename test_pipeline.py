"""Smoke test the supplied hackathon outage scenario and safe remediation flow."""
import automation


def main():
    logs, events = automation.load_scenario()
    result = automation.analyse_at(logs, events, automation.DETECTION_MINUTE)
    assert result["status"] == "incident", result
    assert result["root_service"] == "payment-service", result.get("root_service")
    assert result["cause"] == "Database connection pool exhaustion", result.get("cause")
    remediation = automation.simulate_remediation(result)
    assert remediation["status"] == "resolved"
    assert all(action["status"] == "completed" for action in remediation["actions"])
    assert "PASS" in remediation["verification"]
    print("PASS: outage detected, root cause identified, simulated fix applied, recovery verified.")
    print(f"Root service: {result['root_service']}")
    print(f"Diagnosis: {result['cause']}")
    print(f"Analysed log lines: {result['n_lines']:,}")
    print(f"Simulated actions: {len(remediation['actions'])}")


if __name__ == "__main__":
    main()
