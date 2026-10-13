import argostranslate.package
import argostranslate.translate

PAIRS = [
    ("en", "hi"),
    ("zh", "hi"),
    ("zh", "en"),
    ("en", "zh"),
]


def main():
    print("Updating Argos package index...")
    argostranslate.package.update_package_index()

    available = argostranslate.package.get_available_packages()
    installed = argostranslate.package.get_installed_packages()

    installed_pairs = {
        (package.from_code, package.to_code)
        for package in installed
    }

    for source, target in PAIRS:
        if (source, target) in installed_pairs:
            print(f"Already installed: {source} -> {target}")
            continue

        candidates = [
            package
            for package in available
            if package.from_code == source
            and package.to_code == target
        ]

        if not candidates:
            print(
                f"Direct model unavailable: {source} -> {target}. "
                "Checking other installed routes at runtime."
            )
            continue

        package = candidates[-1]

        print(
            f"Downloading {source} -> {target}, "
            f"version {package.package_version}"
        )

        try:
            package_path = package.download()
            argostranslate.package.install_from_path(package_path)
            print(f"Installed: {source} -> {target}")

        except Exception as exc:
            print(f"Installation failed: {exc}")

    languages = argostranslate.translate.get_installed_languages()

    print(
        "Installed language codes:",
        ", ".join(sorted(language.code for language in languages))
    )

    print(
        "Model installation finished. "
        "Verify the required Chinese/English-to-Hindi routes before "
        "translating a complete novel."
    )


if __name__ == "__main__":
    main()
