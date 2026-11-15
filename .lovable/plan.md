# Heroku deploy fix (AkkiUp bot)

## Asli problem
Aapki ZIP me `requirements.txt` aur `Aptfile` maujood hain, lekin Heroku ko jo code mila (GitHub repo) usme ye dono files nahi hain. Error ki list me bhi `requirements.txt` nahi dikh raha. Yaani ye files GitHub pe upload/commit nahi hui.

Doosri baat: aapke paas `heroku.yml` + `Dockerfile` hai (ffmpeg, N_m3u8DL-RE, mp4decrypt install karta hai). Iske liye Heroku ko Docker (container) mode me chalna chahiye, Python buildpack me nahi. Python buildpack ye tools install nahi karega aur bot download fail karega.

## Steps
1. GitHub repo me `requirements.txt` (aur `Aptfile`) upload karo — root folder me, wahi jahan `main.py` hai. Repo me check karo ki file dikh rahi hai.
2. Heroku stack ko container pe set karo (Heroku CLI):
   `heroku stack:set container -a <app-name>`
   (Ya Heroku Dashboard → Settings → Buildpacks hata do, phir CLI se upar wala command.)
3. Phir se deploy karo (Deploy tab → Deploy Branch, ya `git push heroku main`).
4. Resources tab me `web` aur `worker` dono dynos ON karo, aur Settings → Config Vars me `.env.example` wale saare values bharo.

## Note
Ye code Lovable project me lane ki zarurat nahi — ye Python Telegram bot hai, Lovable sirf web apps banata hai. Fix sirf Heroku/GitHub side pe hai.
