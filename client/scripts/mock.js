import { startMockServer } from '../test/fixtures/mock-server.js'

const { server } = startMockServer({
  port: Number(process.env.CHAT_PORT ?? 8765),
  agentFailure: process.env.MOCK_AGENT_FAILURE,
})
server.on('listening', () => console.log('Local TCP fixture at 127.0.0.1:' + server.address().port))
