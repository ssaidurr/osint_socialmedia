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

| Source | Ki pay | Setup |
|---|---|---|
| Google News | Keyword diye search kora news (English + বাংলা) | Kichu lagbe na |
| News RSS | Prothom Alo, The Daily Star (config-e aro add kora jay) | Kichu lagbe na |
| Reddit | r/bangladesh, r/dhaka-er notun post **ar comment** | Kichu lagbe na (comment feed-e Reddit majhe majhe 429 rate-limit dey. Tokhon post-gulo thik-i save hoy, comment porer run-e ashe) |
| YouTube | Video + comment | `YOUTUBE_API_KEY` (free, niche dekhun) |

Facebook/Instagram rakha hoyni. Meta-r public API nei, ar scraping korle ToS bhange.

## Negative news-er type (category)

অপরাধ ও আইনশৃঙ্খলা (crime & law) · রাজনৈতিক সংঘর্ষ (political unrest) · দুর্ঘটনা (accident) · প্রাকৃতিক দুর্যোগ (disaster) · স্বাস্থ্য (health) · অর্থনীতি (economy) · দুর্নীতি/প্রতারণা (corruption) · নিরাপত্তা/সন্ত্রাস (security) · সামাজিক সমস্যা (social) · খেলাধুলা (sports) · অন্যান্য (other)

Protiti negative item-e **severity 1–5** thake. Category list-ta [osint/analyzer.py](osint/analyzer.py)-er `CATEGORIES`-e.

## Analyzer: tinta option

- **lexicon** (kono key na thakle, free, offline): English-er jonno VADER, ar বাংলা/Banglish-er jonno keyword list. Headline-e bhalo kaaj kore, kintu sarcasm ba context bojhe na.
- **gemini** (recommended, free tier): `.env`-e `GEMINI_API_KEY` dile nije theke chalu hoy. Bangla, Banglish ar context bhalo bojhe, ar protiti item-er ek line-er বাংলা summary dey.
- **claude** (paid): `ANTHROPIC_API_KEY` dile chalu hoy. Output gemini-r motoi.

LLM fail korle (key vul, rate limit, quota shesh, network) oi item-gulo lexicon diye analyze hoy, tai pipeline kokhono atke jay na.

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
- negative share ≥ `negative_ratio_threshold` (40%)
- negative item ≥ `min_negative_count` (10)

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
