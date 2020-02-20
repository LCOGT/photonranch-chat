import boto3, json, logging, time, os, decimal
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger("handler_logger")
logger.setLevel(logging.DEBUG)

dynamodb = boto3.resource("dynamodb")


# Helper class to convert a DynamoDB item to JSON.
class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            if o % 1 > 0:
                return float(o)
            else:
                return int(o)
        return super(DecimalEncoder, self).default(o)

def _get_response(status_code, body):
    if not isinstance(body, str):
        body = json.dumps(body)
    return {
        "statusCode": status_code, 
        'headers': {
            # Required for CORS support to work
            'Access-Control-Allow-Origin': '*',
            # Required for cookies, authorization headers with HTTPS
            'Access-Control-Allow-Credentials': 'true',
        },
        "body": body}


def connection_manager(event, context):
    """
    Handles connecting and disconnecting for the Websocket
    """
    connectionID = event["requestContext"].get("connectionId")
    connectionsTableName = os.getenv("CONNECTIONS_TABLE")
    table = dynamodb.Table(connectionsTableName)

    print(json.dumps(event.get("queryStringParameters", []), indent=2))

    if event["requestContext"]["eventType"] == "CONNECT":
        logger.info("Connect requested")
        room = event["queryStringParameters"]["room"]
        username = event["queryStringParameters"]["username"]

        # Add connectionID to the database
        table.put_item(Item={
            "ChatRoom": room, 
            "ConnectionID": connectionID,
            "Username": username,
            })

        # Update clients in the same room that a new user is online
        users = _get_online_users(room)
        connections = [x["ConnectionID"] for x in users if "ConnectionID" in x]
        logger.debug("Sending online user list: {}".format(users))
        data = {"onlineUsers": users}
        for connectionID in connections:
            try:
                _send_to_connection(connectionID, data, event)
            except: 
                continue

        return _get_response(200, "Connect successful.")

    elif event["requestContext"]["eventType"] in ("DISCONNECT", "CLOSE"):
        logger.info("Disconnect requested")

        # Get the details for the connection ID that is disconnecting
        connections = table.query(
            IndexName="ConnectionID",
            KeyConditionExpression=Key('ConnectionID').eq(connectionID)
        )

        for c in connections['Items']:
            # Remove each connection for the connectionID from the database
            table.delete_item(
                Key={
                    "ChatRoom": c["ChatRoom"],
                    "ConnectionID": connectionID
                }
            ) 

            # Send an updated users list to all clients in the room
            room = c["ChatRoom"]
            users = _get_online_users(room)
            connectionIDs = [x["ConnectionID"] for x in users if "ConnectionID" in x]
            logger.debug("Sending online user list: {}".format(users))
            data = {"onlineUsers": users}
            for connectionID in connectionIDs:
                try:
                    _send_to_connection(connectionID, data, event)
                except: 
                    continue
        
        return _get_response(200, "Disconnect successful.")



    else:
        logger.error("Connection manager received unrecognized eventType '{}'")
        return _get_response(500, "Unrecognized eventType.")

def _get_body(event):
    try:
        return json.loads(event.get("body", ""))
    except:
        logger.debug("event body could not be JSON decoded.")
        return {}

def _send_to_connection(connection_id, data, event):
    gatewayapi = boto3.client(
        "apigatewaymanagementapi",
        endpoint_url = "https://" + event["requestContext"]["domainName"] +
            "/" + event["requestContext"]["stage"]
    )
    try: 
        posted = gatewayapi.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(data, cls=DecimalEncoder).encode('utf-8')
        )
        return posted
    except Exception as e:
        print(f"Could not send to connection {connection_id}")
        print(e)


def _get_online_users(room):
    """
    This is the helper function that returns a list of dicts representing
    the users online in a given room.
    """
    connectionsTableName = os.getenv("CONNECTIONS_TABLE")
    table = dynamodb.Table(connectionsTableName)

    response = table.query(KeyConditionExpression=Key('ChatRoom').eq(room))
    items = response.get("Items", [])

    connections = [
        {
            "Username": x["Username"],
            "ConnectionID": x["ConnectionID"],
        } 
        for x in items if "ConnectionID" in x
    ]
    return connections

def getOnlineUsers(event, context):
    """
    This is the api endpoint which uses _get_online_users().
    """
    room = event["queryStringParameters"].get("room", "error")
    if room == "error":
        return _get_response(400, "Requires query string param 'room'.")
    users = _get_online_users(room)
    return _get_response(200, json.dumps(users, cls=DecimalEncoder))


def send_message(event, context):
    """
    When a message is sent on the socket, forward it to all connections. 
    """
    print(event)

    messagesTableName = os.getenv("MESSAGES_TABLE")
    connectionsTableName = os.getenv("CONNECTIONS_TABLE")

    logger.info("Message sent on WebSocket.")

    #Ensure all required fields were provided
    body = _get_body(event)
    for attribute in ["username", "content", "room"]:
        if attribute not in body:
            logger.debug("Failed: '{}' not in message dict."\
                    .format(attribute))
            return _get_response(400, "'{}' not in message dict"\
                    .format(attribute))

    # Get the next message index
    table = dynamodb.Table(messagesTableName)
    room = body["room"]
    response = table.query(KeyConditionExpression="Room = :room",
            ExpressionAttributeValues={":room": room},
            Limit=1, ScanIndexForward=False)
    items = response.get("Items", [])
    index = items[0]["Index"] + 1 if len(items) > 0 else 0

    # Add the new message to the database
    timestamp = int(time.time())
    username = body["username"]
    content = body["content"]
    table.put_item(Item={"Room": room, "Index": index,
            "Timestamp": timestamp, "Username": username,
            "Content": content})

    # Get all current connections
    users = _get_online_users(room)
    #table = dynamodb.Table(connectionsTableName)
    #response = table.scan(ProjectionExpression="ConnectionID")
    #response = table.query(KeyConditionExpression=Key('ChatRoom').eq(room))
    #items = response.get("Items", [])
    connections = [x["ConnectionID"] for x in users if "ConnectionID" in x]

    # Send the message data to all connections
    message = {
        "username": username, 
        "content": content, 
        "timestamp": timestamp
    }
    logger.debug("Broadcasting message: {}".format(message))
    data = {"messages": [message]}
    for connectionID in connections:
        try: 
            _send_to_connection(connectionID, data, event)
        except:
            continue

    return _get_response(200, "Message sent to all connections.")

def get_recent_messages(event, context):
    """
    Return the 10 most recent chat messages.
    """
    logger.info("Retrieving most recent messages.")
    connectionID = event["requestContext"].get("connectionId")

    #Ensure all required fields were provided
    body = _get_body(event)
    print(json.dumps(body))
    for attribute in ["room"]:
        if attribute not in body:
            logger.debug("Failed: '{}' not in message dict."\
                    .format(attribute))
            return _get_response(400, "'{}' not in message dict"\
                    .format(attribute))

    messagesTableName = os.getenv("MESSAGES_TABLE")
    connectionsTableName = os.getenv("CONNECTIONS_TABLE")

    # Get the 10 most recent chat messages
    table = dynamodb.Table(messagesTableName)
    room = body.get("room")
    response = table.query(KeyConditionExpression="Room = :room",
            ExpressionAttributeValues={":room": room},
            Limit=10, ScanIndexForward=False)
    items = response.get("Items", [])

    # Extract the relevant data and order chronologically
    messages = [{
        "username": x["Username"], 
        "content": x["Content"], 
        "timestamp": x["Timestamp"]}
            for x in items]
    messages.reverse()

    # Send them to the client who asked for it
    data = {"messages": messages}
    _send_to_connection(connectionID, data, event)

    return _get_response(200, "Sent recent messages.")


def default_message(event, context):
    """
    Send back error when unrecognized WebSocket action is received.
    """
    logger.info("Unrecognized WebSocket action received.")
    return _get_response(400, "Unrecognized WebSocket action.")

