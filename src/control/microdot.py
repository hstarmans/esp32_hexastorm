import asyncio


class SSEManager:
    def __init__(self):
        self.connections = []

    async def subscribe(self, sse):
        self.connections.append(sse)
        try:
            # Deze loop houdt de verbinding open
            while True:
                await asyncio.sleep(3600)  # Wacht gewoon
        finally:
            self.connections.remove(sse)

    async def publish(self, data):
        for sse in self.connections:
            try:
                await sse.send(data)
            except:
                pass  # Verbroken verbindingen worden opgeruimd via de subscribe loop


# Maak de instantie aan
sse_manager = SSEManager()
