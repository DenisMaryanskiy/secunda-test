import asyncio

from payment_service.messaging.consumer import create_consumer_app

asyncio.run(create_consumer_app().run())
