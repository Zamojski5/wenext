/** `POST /community/enable` — start observing one community (`POST /api/communities`). */
import { enableCommunity } from "../../../../lib/control-plane";
import { backWith, field, formOf, tokenOrLogin, verdictOf } from "../_handler";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const auth = await tokenOrLogin();
  if ("redirect" in auth) return auth.redirect;
  const form = await formOf(request);
  if (form === null) return backWith("enable_refused");
  const platform = field(form, "platform");
  const communityKey = field(form, "community_key");
  if (platform === "" || communityKey === "") return backWith("enable_refused");
  return verdictOf(await enableCommunity(auth.token, platform, communityKey), "enabled", "enable_refused");
}
