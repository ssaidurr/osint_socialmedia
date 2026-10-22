# OSINT Social Media & News Monitor

Public news o social media theke data collect kore, sentiment analysis kore, negative news-er **type** analytics dey. Negative news beshi hoye gele automatically ekta **ticket** banay ar email kore pathay (default: `ffoisall@gmail.com`).

```
Collect ──► Analyze ──────────────────────► Check threshold ──► Ticket + Email
(news/RSS,   (sentiment, negative-news        (last 6h e negative   (data/tickets/*.html
 Reddit,      type, severity 1–5,             ≥ 40% hole)            + SMTP email)
 YouTube)     Bangla summary)
                    │
                    └──► SQLite (data/osint.db) ──► Dashboard (streamlit)
```

## Ki ki collect hoy

Default-e **global** news ar social media monitor kore. Kono ekta desh, brand ba bishoy-e focus korte chaile [config.yaml](config.yaml)-e query/feed bodle nin (Bangladesh-er feed-gulo comment kora ache, uncomment korlei chalu).

| Source | Ki pay | Setup |
|---|---|---|
| Google News | 32-ta feed: topic (TOP, WORLD, BUSINESS, HEALTH), desh-er edition, ar keyword/`site:` search | Kichu lagbe na |
| News RSS | 53-ta outlet — global wire, Europe, Asia, Africa, Americas, Middle East, Bangladesh, ar science/health/climate/cyber | Kichu lagbe na |
| Reddit | 7-ta subreddit-er post ar comment | Kichu lagbe na |
| Telegram | 5-ta public channel | Kichu lagbe na |
| Mastodon | 16-ta hashtag timeline | Kichu lagbe na |
| YouTube | Video + comment, 3-ta query | `YOUTUBE_API_KEY` (free) |

**Pala kore ana (rotation):** shob source protiti run-e anle dine 50,000+ item hoye jay — sheta free analysis quota-r baire, ar GitHub-e rakha database-er limit-eo chariye jay. Tai protiti run-e ekta ongsho ashe, ar pala ghure shob source cover hoy: Google News 10/32, RSS 18/53, Mastodon 6/16, Reddit 4/14 ([config.yaml](config.yaml)-e `per_run`)।

**Je shima-r modhye thakte hobe:**

| | Hisheb |
|---|---|
| Protiti item | ~300 bytes (gzipped) |
| GitHub-e nirapod shima | ~150,000 item (~45 MB) |
| Ekhonkar obostha | ~5,000 item/din × 21 din = ~50,000 item, 14 MB |

Volume barate chaile `per_run` barao, ar sheই shathe `retention_days` komao — na hole ekdin push fail korbe.

**Blocked outlet-er tricks:** Arab News, Al Arabiya, Gulf News, The National, Haaretz, New Age, bdnews24, Jugantor, Kaler Kantho, Samakal, Ittefaq — egulo nijeder feed-e bot block kore, kintu **Google News-er `site:` search** diye tader khobor-o ashe.

### Ja add kora jay ni (ar keno)

| Platform | Keno na |
|---|---|
| **Facebook / Instagram** | Meta public post porar API bondho kore diyeche (CrowdTangle-o 2024-e bondho). Shudhu **nijer** Page/Business account-er data pora jay. |
| **WhatsApp** | Group ar community end-to-end encrypted. Channel porar public API nei. Shudhu nijer Business number-e asha message pora jay. |
| **Signal** | Kono public content-i nei, shob encrypted. Monitor korar moto kichu nei. |
| **TikTok** | Research API shudhu approved university/NGO-der jonno (mulot US/EU). Baki shob nijer account-er modhhe shimito. |
| **LinkedIn** | Shudhu nijer Company Page, ar tar jonno-o partner approval lage. Public search API nei. |
| **X (Twitter)** | API ache kintu paid ar dami. |

Egulor kono-tateo scraping kora hoyni — ToS bhange, account ban hote pare, ar aini jhuki ache.

## Negative news-er type (category)

অপরাধ ও আইনশৃঙ্খলা (crime & law) · রাজনৈতিক সংঘর্ষ (political unrest) · দুর্ঘটনা (accident) · প্রাকৃতিক দুর্যোগ (disaster) · স্বাস্থ্য (health) · অর্থনীতি (economy) · দুর্নীতি/প্রতারণা (corruption) · নিরাপত্তা/সন্ত্রাস (security) · সামাজিক সমস্যা (social) · খেলাধুলা (sports) · অন্যান্য (other)

Protiti negative item-e **severity 1–5** thake. Category list-ta [osint/analyzer.py](osint/analyzer.py)-er `CATEGORIES`-e.

## Analyzer: charta option

Protiti item-er sentiment, category, country ar ek line-er বাংলা summary — ar "Ek nojore" brief-o ei provider diye-i lekha hoy.

| Provider | Khoroch | Kokhon chalu hoy |
|---|---|---|
| **lexicon** | Free, offline | Kono LLM key na thakle |
| **gemini** (recommended) | Free tier-e chole | `GEMINI_API_KEY` |
| **claude** | Paid | `ANTHROPIC_API_KEY` |
| **openai** | Paid, **kono free tier nei** | `OPENAI_API_KEY` |

`provider: auto` thakle ei krame key khuje ney: Gemini → Claude → OpenAI → lexicon. Jekono ekta jor kore chalate [config.yaml](config.yaml)-e `analyzer.provider` set koro (`gemini` / `claude` / `openai` / `lexicon`)।

**OpenAI babohar korle:** `.env`-e `OPENAI_API_KEY` dao, ar `analyzer.openai_model`-e tomar account-e ache emon model-er nam dao (default `gpt-5.4-mini`)। Model-er nam vul hole run-e sposhto message ashbe, ar oi run-e lexicon-e fallback korbe. Ekhonkar data-te mashe mota-muti 3M input + 2.5M output token lagbe — khoroch jante tomar plan-er per-token dam dekho.

LLM fail korle (key vul, rate limit, quota shesh, network) oi item-gulo lexicon diye analyze hoy, tai pipeline kokhono atke jay na.

## Demo access: ke dekhche, ar koto din

Dashboard-e dhukar age protiti manush ekta chhoto form bhore — **email, nam, phone, country**. Tarpor **14 din** free access pay. Protiti registration ekta **Google Sheet**-e row hishebe jay, tai team Sheet kholei lead-er list dekhte, filter korte ar export korte pare.

- Purono user **shudhu email** dilei dhuke jay — nam/phone/country abar chay na।
- **Ek phone number-e ekta-i trial** — onno email diye abar cheshta korle bole dey kon address-e oi number agei registered. (Email bodle bar bar 14 din neowa bondho।)
- **Admin-ra form-i dekhe na** — tinta upay: (1) app **private** korle Streamlit nije viewer-er email bole dey, oi email `admin_emails`-e thakle shorasori dhuke jay; (2) secrets-e `admin_key = "..."` dile `?admin=<key>` link bookmark kore rakha jay; (3) emni-i oi email likhe Continue.

  Agerca niyom — [config.yaml](config.yaml)-er `demo_access.admin_emails`-e je email thakbe, sheta diye shorasori dhuke jabe, Sheet-e kono row-o hobe na।
- **14 din par hoye gele** data bondho. Screen-e meyad shesh-er kotha ar ekta chhoto form — ki kaaj-e lagbe likhe pathate pare. Oi request Sheet-er **"requests"** tab-e row hoye jay (shomoy, email, nam, phone, country, message), tai follow-up kora shoja.
- Refresh korle login thake — access token URL-e (`?k=...`) thake, tai barbar email dite hoy na।
- Sheet-e `last_seen` ar `visits`-o update hoy, tai bojha jay ke asholey use korche.

Trial-er din bodlate [config.yaml](config.yaml) → `demo_access.trial_days`. Meyad shesh howar message-e je email dekhabe sheta `demo_access.contact_email`-e boshao.

### Setup (ekbar)

1. **Google Sheet banao** (khali)। URL theke ID nao: `docs.google.com/spreadsheets/d/<EI_ONGSHO>/edit`
2. **Google Cloud Console** (jekono project): APIs & Services → Library → **Google Sheets API** → Enable.
3. **Service account banao**: Credentials → Create credentials → Service account → banar por **Keys → Add key → JSON** diye key download koro.
4. **Sheet-ta service account-er email-e share koro** (JSON-er `client_email`), **Editor** access diye। Eta na korle app Sheet-e likhte parbe na।
5. **Streamlit Cloud → app → Settings → Secrets**-e JSON-er field-gulo boshao — format [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example)-e ache.

Header row app nije banie ney, tomake kichu korte hobe na.

### Local-e kaaj korar shomoy

Secrets na thakle app **nije theke bondho thake** (jate deploy-e vul kore gate khola na thake)। Local-e gate chara chalate:

```bash
DEMO_OPEN=1 .venv/bin/streamlit run dashboard.py
```

## Fact check ar credibility

Protiti negative item-er ekta **Credibility score (5–95)** ar tar karon dashboard-e dekha jay.

**Eita "koto percent true" na.** Ajker taja khobor shotti kina, sheta kono AI ba tool nishchit bhabe bolte pare na. Tai ekhane shudhu emon jinish mapa hoy ja asholei mapa jay:

1. **Published fact-check:** Google Fact Check Tools API diye dekha hoy Rumor Scanner, BOOM, AFP-er moto fact-checker-ra ei claim-ta age jachai koreche kina. Match pele verdict ar link dekhano hoy.

   Bhul label boshano shobcheye kharap fol, tai match-er niyom-ta kora: item-er lekha ar fact-check-er (claim + article title) majhe **okkhor-vittik mil ≥ 0.30** na hole match dhora hoy na. Shobdo-vittik mil Bangla-y kaaj kore na ("ইউনূস" vs "ইউনূসের" alada token), ar fact-checker-ra prayi claim English-e ar title Bangla-y lekhe. Threshold-ta asol fact-check jora diye calibrate kora: ekই khobor-er jora peyeche ≥ 0.39, alada khobor-er jora ≤ 0.21. Aro kora korte chaile `config.yaml`-e `fact_check_min_similarity` barao.

   Fact-check-e purono debunk-o kaaj-e lage (gujob fire ashe), tai kono age limit nei. Setting bodlanor por purono item abar jachai korte: `main.py credibility --recheck`.
2. **Corroboration:** Ekই khobor koyta **alada source** diyeche. Headline-er shobdo miliye ber kora hoy, tai alada bhashay lekha holeo dhora pore.
3. **Source-er man:** Established outlet (config-er `trusted_sources`), naki ojana site, naki YouTube/Reddit comment.

Score kokhonoi 0 ba 100 hoy na. Beshi score mane "emon source-e ache jara shadharonoto thik khobor dey, ar onnorao eta dicche". Kom score mane "shondeho koro", **"eita fake" na**. Ar fact-check na pawa mane khobor-ta shotti na — shudhu mane keu eta niye ekhono fact-check kore ni.

Dashboard-er Negative items table-e **"Only doubtful (credibility < 40)"** tick korle shudhu shondehojonok item-gulo dekhabe. Ticket email-eo low-credibility ar fact-check verdict dekhano hoy.

## Ek nojore brief (ki hocche)

Dashboard-er upore ekta card, ar protidin shokale ekta email — shesh **24 ghonta** ar shesh **7 diner** khobor 5–8 line-e. Target holo: ek nojore dekhei bojha jay ki hocche.

```
📰 Ek nojore — what happened          ( Last 24 hours | Last 7 days )
- যুক্তরাষ্ট্রের ওরেগনে স্কুলে গুলিবর্ষণে শিশুসহ চারজন নিহত, ১২টি সূত্র কভার করেছে।
- পেশোয়ারে স্কুল ছাদ ধসে বহু শিশু হতাহত, ৮টি সূত্র জানিয়েছে।
- মোট 1,846 item · 59% negative · দেশ: US, UA, GB · বিষয়: রাজনৈতিক সংঘর্ষ, নিরাপত্তা
Written 16 Oct 2026, 11:48 AM · gemini-3.5-flash-lite
```

Kivabe kaaj kore:

- Ekই khobor onek outlet dile ekbar-i ashe, ar sathe "koyta source diyeche" sheta thake — eta-i bole khobor-ta koto boro.
- Generic title (jemon "আন্তর্জাতিক বাংলা খবর 16 Oct") bad diye dey, karon oita kono ekta ghotona na.
- **Number-gulo code-e hisheb hoy, LLM likhe na.** Ekbar test-e Gemini 57%-ke "৫.৭%" likhe diyechilo — report-e vul number na-thakar cheye kharap kichu nei. Tai LLM shudhu khobor-er bullet likhe, shesh-er totals line code-e toiri hoy.
- LLM key na thakle ba call fail korle **plain version** likhe dey (top story + totals), tai card ar email kokhono khali thake na.

Command:
```bash
.venv/bin/python main.py digest                  # je brief-gulo purono hoye geche, shegulo notun kore lekhe
.venv/bin/python main.py digest --hours 168      # shudhu 7 diner
.venv/bin/python main.py digest --force          # ekhon-i notun kore lekhao
.venv/bin/python main.py digest --email          # rojer email ekhon-i pathanor cheshta
```

Setting [config.yaml](config.yaml) → `digest`: kon window (`windows`), koto ghonta por por notun kore lekha hobe (`every_hours`, protiti brief-e ekta LLM call), email on/off ar kon ghontay pathabe (`email_hour`, tomar `timezone` onujayi).

## Country filter ar keyword search

Dashboard-er upore tinta filter ache: **Time range**, **Source type**, **Country**, ar ekta **Search** box.

- **Country:** protiti item kon desh niye, analyzer sheta ber kore (ISO code, jemon BD, IN, US). Gemini/Claude eta bhalo bojhe (Gaza-ke PS dhore); LLM key na thakle ~50-ta desh-er nam/shohor/neta-r keyword diye ondaj kora hoy. Kono desh-er shathe na milleli khali thake.
- **Search:** headline, text ar summary-te lekha khoje. Bangla-o kaaj kore, jemon `ডেঙ্গু`.
- Filter-gulo chart ar table, duitatei kaaj kore. Ar "Negative items by country" chart-e kon desh-er khobor beshi negative sheta dekha jay.

Purono item-e country khali thakle ekbar chalao (free, LLM call lagbe na): `main.py analyze --countries`.

**Notun keyword collect korte** — mane oi keyword-er notun khobor ana — [config.yaml](config.yaml)-e `google_news.feeds`-e `{ query: "your keyword" }` ar `reddit.searches`-e keyword add koro. Search box shudhu ja collect kora hoyeche tar moddhe khoje.

### Fact Check API enable korte hobe (free)

1. https://console.cloud.google.com/apis/library/factchecktools.googleapis.com e jao (ekই project jekhane YouTube API enable korecho).
2. **Enable** chapo.
3. Key-ta jodi YouTube API-te restrict kora thake, tahole **Credentials → key → Edit → API restrictions**-e "Fact Check Tools API"-o select koro.

Enable na korle baki duita signal (corroboration ar source-er man) thik-i kaaj korbe, shudhu published fact-check match hobe na.

## Setup

```bash
cd osint_socialmedia
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # tarpor .env edit korun
```

### Email (Gmail) setup

1. Je Gmail theke email jabe, tar **2-Step Verification** on korun.
2. https://myaccount.google.com/apppasswords e giye ekta **App Password** banan (16 character).
3. `.env`-e likhun:
   ```
   SMTP_USER=apnar.sender@gmail.com
   SMTP_PASSWORD=abcd efgh ijkl mnop
   ```
4. Test korun: `.venv/bin/python main.py test-email`

### Gemini API key (free)

1. https://aistudio.google.com/apikey e jan, Google account diye login korun.
2. **Create API key** e click kore key-ta copy korun.
3. `.env`-e likhun: `GEMINI_API_KEY=apnar-key`
4. Purono data Gemini diye abar analyze korte: `.venv/bin/python main.py analyze --redo`

Free tier-er limit (per minute/per day) dekhte: https://aistudio.google.com/rate-limit. Limit par hoye gele oi run-er baki item lexicon diye analyze hoy.
**Note:** Free tier-e Google apnar pathano content tader product improve korte use korte pare. Public news-er jonno shomossha nei, kintu private ba confidential data pathaben na.

### YouTube API key (free, credit card lage na)

1. https://console.cloud.google.com e jan. Upore project dropdown theke **New Project** banan (jemon `osint-monitor`).
2. **APIs & Services → Library** e giye "YouTube Data API v3" search korun, tarpor **Enable** korun.
3. **APIs & Services → Credentials → Create credentials → API key** e click korun, key-ta copy korun.
4. (Recommended) Key-er **Edit** e giye **API restrictions → Restrict key → YouTube Data API v3** select kore Save korun.
5. `.env`-e likhun: `YOUTUBE_API_KEY=apnar-key`

Quota: video search dine max **100 call** (protiti query = 1 call). Tai [config.yaml](config.yaml)-e YouTube `every_minutes: 60` e chole (2 query × 24 = 48 call/din). Query barale `every_minutes`-o barun.

## Chalano

```bash
.venv/bin/python main.py run --dry-run   # sab kichu chalay, email pathay na (test-er jonno)
.venv/bin/python main.py run             # collect → analyze → threshold check → email
.venv/bin/python main.py report          # terminal-e last 24h-er analytics
.venv/bin/python main.py watch           # proti 30 min por por nije theke run hoy
.venv/bin/python main.py analyze --redo  # API key add korar ba keyword edit korar por purono data abar analyze
.venv/bin/streamlit run dashboard.py     # browser dashboard (http://localhost:8501)
```

Ticket raise hole email-er ekta copy `data/tickets/<ticket-id>.html`-e save hoy. Dashboard-e "Tickets" section theke o preview kora jay.

### Background-e chalano (cron)

`crontab -e` diye ei line add korun. Proti 30 min por por run hobe:

```
*/30 * * * * cd "/Users/macmini/Documents/coding Project/osint_socialmedia" && .venv/bin/python main.py run >> data/cron.log 2>&1
```

## Alert rule ([config.yaml](config.yaml) → `alert`)

Last `window_hours` (6h)-er moddhe jodi ei tinta shorto ek sathe mile, tahole ticket hobe:
- total analyzed item ≥ `min_items` (20)
- negative share ≥ **alert bar** (niche dekho)
- negative item ≥ `min_negative_count` (10)

**Alert bar ekta fixed number na.** Mepe dekha geche global news-e ghonta-prati negative share 35% theke 59% porjonto otha-nama kore. Fixed number boshale hoy protidin alert jabe, noy kokhono jabe na. Tai bar hoy ei duitar moddhe **jeta beshi**:

- `negative_ratio_threshold` (50%) — ekta absolute floor, ar
- **ei feed-er nijer shesh `baseline_days` (7 din)-er normal + `baseline_delta` (12 point)**

Jemon: shesh 7 diner normal 37% hole bar hobe 50% (floor-ta beshi). Normal 48% hole bar hobe 60%. Terminal ar dashboard duitatei bar ar normal dekhano hoy, ar email-eo "Normal for the last 7 days is X%" lekha thake.

Purono fixed-threshold behaviour chaile `baseline_delta: 0` kore dao.

Spam atkate `cooldown_hours` (6h)-er moddhe notun ticket hobe na. Tobe negative share `escalation_delta` (15 percentage point) bere gele notun ticket hobe. Ticket level `MEDIUM`, `HIGH` ba `CRITICAL` hoy, negative share ar average severity dekhe.

## GitHub-e chalano (free, computer on rakhte hobe na)

```
GitHub Actions (proti ghonta)             repo-r `data` branch          Streamlit Cloud
collect → analyze → email ticket ──push──► data/osint.db ◄──read── dashboard (browser-e)
```

Workflow file: [.github/workflows/monitor.yml](.github/workflows/monitor.yml). Database repo-r alada `data` branch-e thake. Protibar shudhu latest copy-ta rakha hoy, tai repo boro hoy na. 30 diner purono data nije theke muche jay (`retention_days`).

### 1. GitHub-e repo banan ar code push korun

https://github.com/new e giye `osint_socialmedia` naame repo banan (Private ba Public). README/.gitignore add korben na. Tarpor terminal-e:

```bash
cd "/Users/macmini/Documents/coding Project/osint_socialmedia"
git init -b main
git add .
git status          # check korun: .env, .venv/, data/ jeno list-e NA thake
git commit -m "OSINT monitor"
git remote add origin https://github.com/<apnar-username>/osint_socialmedia.git
git push -u origin main
```

Push korar shomoy password chaile GitHub password na, **Personal Access Token** dite hobe (https://github.com/settings/tokens → *Generate new token (classic)* → `repo` + `workflow` scope tick). Shohoj bikolpo: `brew install gh && gh auth login`, tarpor `gh repo create osint_socialmedia --private --source=. --push`.

### 2. Secrets add korun

Repo → **Settings → Secrets and variables → Actions → New repository secret**. `.env`-er value-guloi din:

| Secret | Lagbe? |
|---|---|
| `GEMINI_API_KEY` | Recommended |
| `YOUTUBE_API_KEY` | Optional |
| `SMTP_USER`, `SMTP_PASSWORD` | Email-er jonno lagbe |

### 3. Prothom run

Repo → **Actions** tab → **OSINT monitor** → **Run workflow**. 1–2 minute por sobuj ✓ hole kaaj korche. Log-e `provider: gemini` dekhun. Er por theke protiti ghontay nije theke chalbe. Fail korle GitHub apnake email korbe.

- **Public repo** hole `monitor.yml`-e `cron: "*/30 * * * *"` dite paren (30 min por por). **Private repo**-te mashe free minute limit ache, tai hourly-i rakhun.
- GitHub-e chalano shuru korle nijer computer-er cron/`watch` bondho korun. Na hole duita alada database hobe, ar email duibar ashte pare.
- GitHub-er server theke **Reddit** onek shomoy block hoy. News, RSS ar YouTube thik-i chole.

### 4. Dashboard: Streamlit Community Cloud (free)

1. https://share.streamlit.io e GitHub diye login korun → **Create app** → apnar repo, branch `main`, file `dashboard.py`.
2. **Advanced settings**-e Python 3.13 select korun, ar **Secrets**-e likhun:
   ```toml
   GITHUB_DATA_REPO = "apnar-username/osint_socialmedia"
   GITHUB_TOKEN = "github_pat_..."
   ```
   `GITHUB_TOKEN` banate: https://github.com/settings/personal-access-tokens → **Fine-grained token** → *Only select repositories* → ei repo → Permissions: **Contents: Read-only**. Private repo-te eta lagbei. Public repo-te na dileo chole, tobe dile rate limit-e pore na.
3. **Deploy** e click korun. Dashboard proti 5 minute por por GitHub theke notun data ane.

Private repo-r app default-e private thake. Onno karo dekhate chaile app-er **Settings → Sharing** e tar email invite korun.

## Aina o nitimala

- Shudhu **public** data collect kora hoy. Private account, group ba inbox-e kono access nei.
- Uddeshsho **aggregate/topic-level** monitoring. Kono nirdishto manush-ke track ba surveillance korar jonno eita use korben na.
- Platform-er Terms of Service ar Bangladesh-er Cyber Security/Personal Data aain mene cholun.
