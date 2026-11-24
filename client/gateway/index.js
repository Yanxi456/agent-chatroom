import { startGateway } from './server.js'

const gateway = startGateway()
gateway.on('listening', () => console.log('WebSocket gateway on port', gateway.address().port))
