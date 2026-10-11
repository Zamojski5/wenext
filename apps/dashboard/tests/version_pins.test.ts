/**
 * The Node/Next pins in this package must be the ones in `versions.lock.toml`, not a second
 * opinion about them.
 *
 * `tools/dev/check_versions.py` already refuses a Dockerfile that writes `node:<other
 * version>` — but it never reads `package.json`, so `engines`, the pinned `next` and the image
 * digest could drift from the lock while `make check-versions` stayed green. That is the shape
 * of the bug this repository keeps finding: a convention nobody tests quietly stops applying
 * (D-014 says it about the port invariant, `docs/STAN-PRAC.md` section 7 about
 * `rename_identifiers.py`).
 *
 * The lock is parsed with a regex rather than a TOML library on purpose — adding a dependency
 * to read the file that pins dependencies is a loop this package does not need. Every value is
 * REQUIRED: a pin that cannot be read throws, so a renamed section fails loudly instead of
 * comparing nothing.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const APP_DIR = path.resolve(import.meta.dirname, "..");
const REPO_ROOT = path.resolve(APP_DIR, "..", "..");

const LOCK_TEXT = fs.readFileSync(path.join(REPO_ROOT, "versions.lock.toml"), "utf8");
const PACKAGE_JSON = JSON.parse(
  fs.readFileSync(path.join(APP_DIR, "package.json"), "utf8"),
) as Record<string, Record<string, string>>;
const DOCKERFILE = fs.readFileSync(path.join(APP_DIR, "Dockerfile"), "utf8");

function pin(key: string): string {
  const match = new RegExp(`^\\s*${key}\\s*=\\s*"([^"]+)"`, "m").exec(LOCK_TEXT);
  if (match?.[1] === undefined) {
    throw new Error(`versions.lock.toml nie podaje '${key}' — nie ma z czym porównywać`);
  }
  return match[1];
}

/**
 * Every `FROM` in the Dockerfile, paired with the image reference it names.
 *
 * WHY A PARSER AND NOT `DOCKERFILE.toContain(image@digest)`: that was the check here, and ONE
 * occurrence satisfied it. The image has two stages; with the digest present on the runtime
 * stage, the builder stage could have been written `FROM node:24.20.0-trixie-slim` — a floating
 * tag — and this test stayed green. `tools/dev/check_versions.py` does not catch that either: it
 * matches `node:(\d+\.\d+\.\d+)` and compares the TAG (section 3 of that file), so a tag-only
 * `FROM` is exactly the shape it approves — verified by running it against a Dockerfile with the
 * digest removed: exit code 0, "check-versions: OK". This test is therefore the ONLY place the
 * NODE image's digest is checked at all (`grep -rIn node_image_digest` finds the lock, the
 * Dockerfile, `tools/node.sh` which consumes it, and this file). The comments in the Dockerfile
 * and in `apps/dashboard/NOTES.md` were corrected to stop claiming a second guard for it.
 *
 * `--platform=` is tolerated because BuildKit allows it before the reference; a stage built on
 * an earlier stage (`FROM builder AS …`) is NOT, and that is deliberate — every stage of this
 * image starts from the pinned base, and adding one that does not has to be a conscious edit to
 * this test rather than a silent hole (the rule D-014 applies to the published-ports list).
 */
function fromReferences(): readonly { readonly line: string; readonly image: string }[] {
  const found: { line: string; image: string }[] = [];
  for (const line of DOCKERFILE.split("\n")) {
    const match = /^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)/i.exec(line);
    if (match?.[1] !== undefined) {
      found.push({ line: line.trim(), image: match[1] });
    }
  }
  return found;
}

describe("versions.lock.toml [stack.js]", () => {
  it("the_lock_still_declares_every_javascript_pin", () => {
    // Read first, compare later: five values, all of them mandatory. A section renamed in the
    // lock would otherwise make the comparisons below run against `undefined`.
    const pins = {
      node: pin("node"),
      npm: pin("npm"),
      node_image: pin("node_image"),
      node_image_digest: pin("node_image_digest"),
      next: pin("next"),
    };
    expect(Object.values(pins).every((value) => value.length > 0)).toBe(true);
    expect(Object.keys(pins).length).toBe(5);
  });

  it("package_json_engines_repeat_the_locked_node_and_npm", () => {
    expect(PACKAGE_JSON["engines"]?.["node"]).toBe(pin("node"));
    expect(PACKAGE_JSON["engines"]?.["npm"]).toBe(pin("npm"));
  });

  it("the_pinned_next_version_is_the_one_in_the_lock", () => {
    expect(PACKAGE_JSON["dependencies"]?.["next"]).toBe(pin("next"));
  });

  it("every_stage_of_the_dockerfile_starts_from_the_locked_image_by_digest", () => {
    const expected = `${pin("node_image")}@${pin("node_image_digest")}`;
    const stages = fromReferences();

    // COUNT BEFORE PREDICATE. A regex matching nothing — a moved file, a `FROM` spelled in a
    // way this parser does not read — would leave the loop below asserting nothing and reading
    // exactly like a pass. That is `all([]) == True`, four times paid for in this repo (D-011).
    // Two is both the floor and the current number of stages: builder and runtime.
    expect(stages.length).toBeGreaterThanOrEqual(2);

    for (const stage of stages) {
      expect(stage.image, `FROM nie jest przypięty po digeście: ${stage.line}`).toBe(expected);
    }
  });

  it("no_node_tag_anywhere_in_the_dockerfile_differs_from_the_lock", () => {
    // The tag, separately from the digest, and over the WHOLE file — comments included, because
    // a comment naming another version is how the next reader learns the wrong pin.
    // `tools/dev/check_versions.py` applies this same rule from the outside; for the tag (and
    // only for the tag) there really are two guards, so removing one still leaves the other.
    const tags = Array.from(DOCKERFILE.matchAll(/\bnode:(\d+\.\d+\.\d+)/g)).map(
      (match) => match[1],
    );

    // Two `FROM` lines carry the reference, so anything below two means the scan lost the file.
    expect(tags.length).toBeGreaterThanOrEqual(2);
    for (const tag of tags) {
      expect(tag).toBe(pin("node"));
    }
  });
});
