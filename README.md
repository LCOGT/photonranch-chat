# Photon Ranch Chat (Unused)

This repository manages the dynamoDB tables and websockets associated with the unused chat function described in `ChatModule.vue` in [the frontend](https://github.com/LCOGT/ptr_ui).

This supports a text chatroom for each site which is shared between all online users at a given site. 
There are endpoints for sending messages, retrieving a list of online users, and retrieving the last 10 
messages. No authentication is required to view or post messages anonymously, but authentication allows 
a user's username to be associated with their messages.

Repository has been archived and is read only, as changes to the control room model and the usage of 
RemoteHQ have rendered this repository obsolete.