/** `POST /community/profile` — a new version of the user's profile (`POST /api/user-profile`). */
import { saveUserProfile } from "../../../../lib/control-plane";
import { backWith, field, formOf, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const auth = await tokenOrLogin();
  if ("redirect" in auth) return auth.redirect;
  const form = await formOf(request);
  if (form === null) return backWith("profile_refused");
  const profile = {
    knows_deeply: field(form, "knows_deeply"),
    positions: field(form, "positions"),
    would_never_claim: field(form, "would_never_claim"),
    voice_samples: field(form, "voice_samples"),
  };
  if (profile.knows_deeply === "" || profile.positions === "" || profile.would_never_claim === "") {
    return backWith("profile_refused");
  }
  return verdictOf(await saveUserProfile(auth.token, profile), "profile_saved", "profile_refused");
}
