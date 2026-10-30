web: uvicorn cgroup.web.app:app --host 0.0.0.0 --port $PORT
worker: python -m cgroup.bot.main
shopweb: uvicorn shop.web:app --host 0.0.0.0 --port $PORT
shopbot: python -m shop.bot
