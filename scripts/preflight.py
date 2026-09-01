#!/usr/bin/env python3
"""RideBridge Lite submission/Raspberry Pi preflight checks."""

import argparse
import importlib
import json
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LANGUAGES = {"ko", "en", "ja", "zh"}


class CheckReport:
    def __init__(self):
        self.errors = []
        self.hardware_warnings = []

    def ok(self, label, detail):
        print("[OK]   {}: {}".format(label, detail))

    def warn_hardware(self, label, detail):
        self.hardware_warnings.append("{}: {}".format(label, detail))
        print("[WARN] {}: {}".format(label, detail))

    def error(self, label, detail):
        self.errors.append("{}: {}".format(label, detail))
        print("[FAIL] {}: {}".format(label, detail))


def check_python(report):
    version = sys.version_info[:3]
    if version < (3, 7):
        report.error("Python", "3.7 이상이 필요합니다: {}".format(version))
    else:
        report.ok("Python", ".".join(str(part) for part in version))


def check_packages(report):
    for module in ("flask", "requests"):
        try:
            imported = importlib.import_module(module)
            version = getattr(imported, "__version__", "installed")
            report.ok("package {}".format(module), version)
        except Exception as exc:
            report.error("package {}".format(module), str(exc))

    backends = []
    for module in ("gpiozero", "RPi.GPIO"):
        try:
            importlib.import_module(module)
            backends.append(module)
        except Exception:
            pass
    if backends:
        report.ok("GPIO library", ", ".join(backends))
    else:
        report.warn_hardware("GPIO library", "gpiozero/RPi.GPIO를 불러올 수 없습니다")


def check_data(report):
    required = ("phrases.json", "corrections.json", "entities.json", "guides.json")
    for filename in required:
        path = DATA_DIR / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload in ({}, []):
                raise ValueError("내용이 비어 있습니다")
            report.ok("data/{}".format(filename), "valid JSON")
        except Exception as exc:
            report.error("data/{}".format(filename), str(exc))

    try:
        phrases = json.loads((DATA_DIR / "phrases.json").read_text(encoding="utf-8"))
        records = list(phrases.get("passenger", []))
        for group in phrases.get("driver_context", {}).values():
            records.extend(group)
        missing = [
            item.get("id", "<unknown>")
            for item in records
            if set(item.get("translations", {})) != LANGUAGES
        ]
        if missing:
            report.error("phrase translations", "누락 ID: {}".format(", ".join(missing)))
        else:
            report.ok("phrase translations", "KO/EN/JA/ZH complete")
    except Exception:
        pass


def check_pi_hardware(report):
    gpiochip = Path("/dev/gpiochip0")
    if gpiochip.exists():
        report.ok("GPIO device", str(gpiochip))
    else:
        report.warn_hardware("GPIO device", "/dev/gpiochip0 없음 (개발 PC라면 정상)")

    try:
        import grp

        group_names = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
        if "gpio" in group_names:
            report.ok("GPIO permission", "현재 사용자가 gpio 그룹에 포함됨")
        else:
            report.warn_hardware("GPIO permission", "현재 사용자가 gpio 그룹에 없음")
    except Exception as exc:
        report.warn_hardware("GPIO permission", "확인 불가: {}".format(exc))

    if shutil.which("wpctl"):
        report.ok("audio selector", "wpctl available")
    else:
        report.warn_hardware("audio selector", "wpctl 없음 (Pi OS 오디오 메뉴 사용 가능)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict-hardware",
        action="store_true",
        help="GPIO/오디오 경고도 실패로 처리합니다.",
    )
    args = parser.parse_args()

    report = CheckReport()
    check_python(report)
    check_packages(report)
    check_data(report)
    check_pi_hardware(report)

    print()
    if report.errors:
        print("Preflight FAILED: {} core error(s)".format(len(report.errors)))
        return 1
    if args.strict_hardware and report.hardware_warnings:
        print("Preflight FAILED: {} hardware warning(s)".format(len(report.hardware_warnings)))
        return 2
    print(
        "Preflight PASSED{}".format(
            " with {} hardware warning(s)".format(len(report.hardware_warnings))
            if report.hardware_warnings
            else ""
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
