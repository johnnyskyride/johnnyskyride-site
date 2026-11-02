# johnnyskyride.com — Owner's Guide

Written Nov 1, 2026. Last verified against the live setup on Nov 1, 2026 (repo, Supabase, Stripe, domain records).
This guide is for Johnny, or for anyone he hires. It is plain English. It contains **no passwords, keys or customer details**, and it never should, because this repository is **public**.

---

## 1. Map of everything

| Piece | Where it lives | Account |
|---|---|---|
| Website code | GitHub repo `johnnyskyride/johnnyskyride-site`, branch `main` | GitHub user `johnnyskyride` |
| Hosting | GitHub Pages (publishes `main` automatically, free) | same GitHub account |
| Domain `johnnyskyride.com` | Registered at **GoDaddy**; DNS also at GoDaddy (nameservers `ns43/ns44.domaincontrol.com`). The `CNAME` file in the repo tells GitHub Pages to use this domain. | GoDaddy account (check which login) |
| Database, member logins, posts, comments | Supabase project `wsrzkxmwufwceqqbstrj` (org "johnnyskyride", **Free plan**) | johnnyskyride@outlook.com (check) |
| Download files | Supabase Storage bucket `store-private` (private) | same |
| Delivery robot | Supabase Edge Function `store-fulfill` | same |
| Payments | Stripe live account `acct_1U8ZimRyFwX44bse` (Payment Links) | johnnyskyride@outlook.com (check) |
| Delivery emails | Resend (sends from `notify@johnnyskyride.com`) | check which login owns the Resend account |
| SkyForge on itch.io | johnnyskyride.itch.io | itch.io account |

The site owner is **johnnyskyride@outlook.com**, and that must stay the **only** owner everywhere.

### Supabase at a glance
- **Tables:** `releases`, `store_products`, `orders`, `members`, `posts`, `post_likes`, `comments`, `comment_likes`, `articles`, `article_comments`, `gallery`, `notifications`, `site_settings`, `bot_emails`, `app_secrets`.
- **Storage buckets:** `media` (public: images/audio shown on the site), `releases` (private), `store-private` (private: paid downloads).
- **Edge Functions:** `store-fulfill` (Stripe calls it after a purchase; it emails the buyer download links), `store-admin` (owner-only store helper), `notify` (sends site notification emails). Functions named `tmp-...` are leftover one-time upload helpers; they are harmless and can be deleted in the dashboard (Edge Functions → function → Delete).
- **Scheduled job (cron):** `release-scheduled`, runs every minute (`select public.release_scheduled()`), handles timed releases.
- **Secret names (values never written down here):** function secrets `RESEND_API_KEY`, `NOTIFY_FROM` (optional), plus Supabase's built-in `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`; database table `app_secrets` holds `stripe_whsec_store` (the Stripe webhook signing secret).

### Stripe at a glance
- Webhook endpoint "johnnyskyride.com store delivery" → `https://wsrzkxmwufwceqqbstrj.supabase.co/functions/v1/store-fulfill`, events `checkout.session.completed` and `checkout.session.async_payment_succeeded`.
- A second webhook "Grok automations" points at grok.com. If you stop using Grok, you can disable it.
- Every product (each $2 single, SkyForge $26, SkyForge Mobile $9.99 and its sale link) is a **Payment Link**. The Buy buttons on the site are just links to them.

---

## 2. How the site is built

- `index.html` is the whole site (all sections, styles and code in one file).
- After editing it, run `python3 tools/build-pages.py`. It copies `index.html` into each section's page so link previews work: `music/`, `collections/`, `imagery/`, `articles/`, `members/`, `sessions/`, and the store pages `store/`, `store/skyforge/`, `store/skyforge-mobile/`, `store/sessions/`. Commit `index.html` **together with** all those generated files.
- `tools/build-shares.py` makes the share-preview pages in `/p/` (posts), `/c/` (comments), `/ac/` (article comments) and images in `/share/`. You never run this yourself: the GitHub Action `.github/workflows/share-pages.yml` runs it every ~5 minutes and commits "share pages: update previews".
- `thanks.html`, `404.html`, icons and `assets/` (images) are plain files.

### Recipe 2a: edit on github.com with no tools
1. Sign in to github.com, open the repo, click `index.html`, click the pencil (Edit).
2. Use Ctrl+F to find the text, change it, click **Commit changes** (to `main`).
3. The site updates in 1–3 minutes.
4. **Catch:** the section pages (e.g. `/store/skyforge/`) won't get the change until `build-pages.py` runs. The homepage and in-site navigation show it right away; only direct links to section pages and preview cards lag. No GitHub Action runs `build-pages.py` today. A freelancer can either run it once (`python3 tools/build-pages.py`, then commit) or add a small Action that runs it on every push to `index.html`.

---

## 3. The Store

How a sale works:
1. Buyer clicks Buy → Stripe Payment Link → pays (tax included).
2. Stripe calls `store-fulfill`. It checks Stripe's signature, finds the `store_products` row whose `payment_link` matches, records the sale in `orders`, and emails the buyer through Resend with download links that **work once and expire in 7 days**. Files come from the `store-private` bucket. Each link looks like `https://johnnyskyride.com/dl/?t=…`; the page there (file `dl/index.html`) asks the `dl` edge function to use up the link and hand over a 5-minute file link. Opening the page doesn't use the link up; only tapping Download does. Links live in the `download_tokens` table (`used_at` shows when one was used).
3. Buyer is sent back to the site with a thank-you banner.

Store rows (`store_products`, column `item`): eight $2 singles, `skyforge`, `skyforge-mobile`, `skyforge-mobile-sale`. The `skyforge` row delivers **two files**: `skyforge/SkyForge-VST3.zip` and `skyforge-mobile/SkyForge-Mobile.apk` (the Android app is included). Music singles also get a 320 kbps MP3 when the download is FLAC.

### Recipe 3a: swap the SkyForge Mobile APK
1. Supabase dashboard → Storage → `store-private` → folder `skyforge-mobile`.
2. Download the current `SkyForge-Mobile.apk` first as a backup.
3. Upload the new file with the **exact same name** `SkyForge-Mobile.apk`, choosing replace/overwrite (or delete the old one, then upload).
4. Edit `index.html` on GitHub: find `Installs as an APK (version` and change the version number.
5. Run the build (Recipe 2a, point 4). Both SkyForge Mobile and SkyForge buyers now get the new APK.
6. **Tell the in-app updater about it** (otherwise phones never see the new build): Supabase → SQL Editor, run the "Publish a new app release" snippet under *Update codes* below with the new version name and versionCode.
7. The public `skyforge-feed` `version.json` is separate. Until it is retired, update it there too if the app still reads it.

### Recipe 3b: swap the SkyForge VST3 zip
Same as 3a, folder `skyforge`, file name `SkyForge-VST3.zip`.

### Recipe 3c: change a price
1. Stripe → Payment Links → **Create** a new link for the product at the new price (keep "tax included" the same as the old one). Stripe prices can't be edited in place.
2. Supabase → Table Editor → `store_products` → the product's row → put the new link's ID (`plink_...`) in `payment_link`. *Without this, buyers pay but get no email.* (For a temporary sale, add a new row instead, like `skyforge-mobile-sale`.)
3. Edit `index.html`: replace the old `https://buy.stripe.com/...` URL and the price text (e.g. `Buy $26`).
4. Run the build. Then in Stripe, deactivate the old link (3e) after a day.

### Recipe 3d: add a new single / timed release
- Releases are rows in the `releases` table: `slug`, `release_at` (when it goes live), `section` (`solo` or `band`), and `data` (title, meta, cover, preview, price, buy_url, links, spotify). The site checks every 60 seconds and shows a release once its time passes; the "Solo singles" count updates itself.
- Easiest: in Table Editor, duplicate an existing row (e.g. `oceans-deep`) and change the values. Cover/preview files go in the `media` bucket.
- To sell it: make a $2 Payment Link in Stripe, upload the audio to the private bucket (check an existing single's `file_path` for the folder), and add a `store_products` row copying an existing single (kind, title, artist, payment_link, file_path, download_name, release_slug).

### Recipe 3e: turn off a payment link
Stripe → Payment Links → open the link → **Deactivate**. Anyone clicking it sees "no longer available." Nothing on the site breaks, but remove the button.

### Recipe 3f: a buyer says no email arrived
1. Ask them to check spam.
2. Supabase → `orders` table: find the order by time. `status`, `buyer_emailed_at` and `error` tell you what happened.
3. Stripe → Developers → Webhooks → store delivery endpoint → recent deliveries: was it sent and did it get 200?
4. Resend dashboard → Emails/Logs: was it sent or bounced?
5. Resend: in Stripe's webhook view, click **Resend** on that event. If the order already shows emailed, the function may not send twice (check this). Fallback: issue a fresh link (see "Reissue a download link" below) and email it yourself from johnnyskyride@outlook.com.

---

### Reissue a download link

If a buyer's link was used up or expired, make a new one. Supabase → SQL Editor, paste, change the file path, name and label to the file they need, then Run:

```sql
insert into public.download_tokens (token, session_id, item, file_path, download_name, label)
values (
  replace(replace(rtrim(encode(gen_random_bytes(24), 'base64'), '='), '+', '-'), '/', '_'),
  'manual',                                   -- or the buyer's checkout session id from `orders`
  'skyforge-mobile',                          -- item
  'skyforge-mobile/SkyForge-Mobile.apk',      -- file path in store-private
  'SkyForge-Mobile.apk',                      -- file name the buyer gets
  'SkyForge Mobile (Android APK)'             -- button label
)
returning 'https://johnnyskyride.com/dl/?t=' || token as link;
```

Copy the `link` it returns and email it. It works once and expires in 7 days. To see the file paths a product uses: `select item, file_path, files from store_products;`

Limit: single-use stops a forwarded email from working, but a buyer who downloads once can still share the file itself. Nothing on a website can prevent that.

### Update codes (SkyForge Mobile in-app updates)

Every SkyForge Mobile or SkyForge purchase email includes one personal update code like `SF-7K2M-9QXD` (table `app_licenses`, one per order). The app sends it to the `app-update` function. The app's background checks are free. Only tapping **Update** uses one update and gets a 5-minute download link to the private APK. Each code allows 25 updates by default, and at most 5 downloads per hour. A code can still be shared; the cap and turning a code off are the controls. Run these in Supabase → SQL Editor.

Look up a buyer's code (to re-send it if they lost it):
```sql
select code, active, updates_used, max_updates, last_used_at, created_at
from public.app_licenses where buyer_email ilike 'buyer@example.com';
```

Make a code for someone by hand (e.g. a gift or a manual sale):
```sql
select public.app_license_issue('manual-' || gen_random_uuid(), 'buyer@example.com', 'skyforge-mobile') as code;
```

Turn a code off (leaked or refunded), or back on:
```sql
update public.app_licenses set active = false, notes = 'leaked' where code = 'SF-XXXX-XXXX';
```

Give a code more updates:
```sql
update public.app_licenses set max_updates = max_updates + 25 where code = 'SF-XXXX-XXXX';
```

Publish a new app release (after uploading the APK, Recipe 3a). Use the app's real version name and versionCode:
```sql
insert into public.app_releases (version, code, file_path, notes)
values ('2.12.25', 21225, 'skyforge-mobile/SkyForge-Mobile.apk', 'What changed, one line.');
```
Phones with a lower versionCode get the update next time they check. The app always serves the newest active row; to roll back, `update public.app_releases set active = false where code = 21225;` and put the old APK back. (Optional: upload to a versioned path such as `skyforge-mobile/SkyForge-2.12.25.apk` and use that as `file_path`, so older files stay available.)

## 4. Nov 4, 2026 sale cleanup (6:00 AM Central)

The page **switches prices by itself** at 6:00 AM Central on 11/4: the itch.io sale tag hides, SkyForge Mobile shows $9.99 and its Buy button points to the regular link. The cleanup below is tidying only.

- [ ] In `index.html`, delete the line `<a class="sf-sale" id="sf-sale" ...>🔥 Sale on itch.io through 11/4...</a>`.
- [ ] Change `$26 <s>$29 on itch.io</s>` to just `$26`.
- [ ] Delete `<span class="sf-sale" id="sm-sale">🔥 33% off through 11/4</span>`.
- [ ] Set the SkyForge Mobile price text (`id="sm-price"`) to `$9.99`, and the `id="sm-buy"` button to `https://buy.stripe.com/bJebJ3fFM5tm4Gu1mQ4ow0f` with text `Buy $9.99`.
- [ ] In the `skyforgeStore` code near the bottom, delete the `sf-sale` auto-hide line and the `if (Date.now() >= Date.UTC(2026, 10, 4, 11, 0, 0)) { ... }` block (comments there say exactly this).
- [ ] Swap the Halloween hero image `/assets/skyforge-halloween.jpg` for `/assets/skyforge-stone.jpg` (hero and first thumbnail).
- [ ] Rebuild the SkyForge share image `assets/og-skyforge.jpg` from the stone cover (a freelancer job; optional).
- [ ] Run `python3 tools/build-pages.py`, commit everything.
- [ ] Stripe: deactivate the sale link `plink_1UWVvLRyFwX44bseyR8wGnGZ`. **Leave** the regular link `plink_1UWVvJRyFwX44bse6fmokyHY` active.
- [ ] Keep the `skyforge-mobile-sale` row in `store_products` active for a day or two (late checkouts still get delivered), then set `active` to false.

---

## 5. Posts, members, comments

- Members sign in through Supabase Auth; profiles are in `members` (emails are hidden on the site).
- Posts, likes and comments live in `posts`, `post_likes`, `comments`, `comment_likes`; articles in `articles` / `article_comments`; images in `gallery`.
- To remove something: Supabase → Table Editor → table → find the row → delete. (Back up first by exporting the table as CSV.)
- Owner rule: johnnyskyride@outlook.com is the owner and must be the **only** owner. Never give anyone else owner/admin rights on the site; give helpers access through each service's team invite instead.

---

## 6. Costs and renewals

| Service | Cost today | If it lapses |
|---|---|---|
| GitHub + Pages | Free | — |
| Supabase | Free plan | Free projects can be **paused after inactivity** (check Supabase's current rule). Paused = logins, posts, store delivery stop; un-pause in the dashboard. |
| Stripe | No monthly fee; a % per sale | — |
| Resend | Free tier likely (check plan) | Buyers stop getting download emails |
| Domain (GoDaddy) | Yearly fee; **expires Apr 7, 2027** | Site disappears at johnnyskyride.com. Turn on auto-renew. |
| itch.io | Free | — |
| Grok Bots | Optional | Site keeps running; only the Nov 4 tidy-up and new changes need a person |

---

## 7. If something breaks

- **Site down/blank:** GitHub repo → Actions and Settings → Pages: is the last deploy green? Undo the last commit (open it → "Revert").
- **Domain not loading:** GoDaddy: is it paid? Is the `CNAME` file still in the repo?
- **No download email:** Recipe 3f.
- **Logins/posts broken:** Supabase project paused or over free limits? Check the dashboard.
- **Wrong price:** Recipes 3c/3e.
- **Bad edit:** every change is saved in GitHub history; revert it.

### How to hire help
Point them to this file. Invite them, don't share passwords: GitHub (repo Settings → Collaborators), Supabase (Organization → Team → Invite, Developer role), Stripe (Settings → Team → Invite, a limited role), Resend/GoDaddy only if needed (both have delegate/team access). Never paste passwords or keys in chat or email. Remove their access when done.

---

## 8. Glossary

- **Repo:** the folder of website files on GitHub. **Commit:** a saved change. **Push:** sending it to GitHub.
- **GitHub Pages:** free hosting that publishes the repo. **GitHub Action:** a robot that runs a script on GitHub.
- **Supabase:** the database + login + file storage behind the site. **Bucket:** a storage folder.
- **Edge Function:** a small program in Supabase (e.g. the one that emails downloads).
- **Payment Link:** a Stripe checkout URL for one product. **Webhook:** Stripe telling the site "someone paid."
- **Resend:** the email-sending service. **DNS / registrar:** the address book for the domain, at GoDaddy.
- **APK:** an Android app file. **VST3:** the Windows plugin format.
