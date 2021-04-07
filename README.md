# GGServer: The Generic Game Server

I've seen people steer away from adding networked multiplayer to their games because of perceived difficulty writing and running the server.  Maybe you don't know much about writing servers.  Maybe it's annoying because you write a server and then it crashes and you have to go restart it.  Maybe you don't like having to keep restarting the server with every new version of your game.  Maybe you don't even have a server handy to run your code on.

The Generic Game Server is meant to ease the pain a bit.  While it can be extended with game-specific functionality, the idea is that in many cases this is not necessary.  In many cases, *all the game state and logic can be put on the clients*.  All you really need the server for is letting you actually match the clients up and pass data to each other.  So that's what GGS does.


## Crash course

GGS keeps track of multiple game types (that is, types of games... e.g., tic-tac-toe and Connect Four).  Within each game type, it keeps track of multiple rooms.  When a client join a game, it is put into a non-full room.  If all the rooms are full, a new room is created.  Once in a room, clients can communicate with each other as well as see which other clients are in the room and so forth.

Each room has a leader; if the leader leaves, GGS elects another player in the room to be the leader.  This doesn't actually mean very much to GGS, but clients may change their behavior based on who the leader is.  For example, *one* way of writing a game using GGS is essentially to have a (conceptual) server -- it just so happens that the server is one of the clients (the one that GGS has indicated is the leader).  The client code and the "server" code within the client can be quite distinct within the code.


## Writing a Client

GGS uses TCP, and defaults to listening on port 9876.  The client and server send messages to each other -- there are server-to-client messages ("server messages") and client-to-server messages ("client messages").  They all have a *type* and some other attributes.  As mentioned, for the most part, the actual GGS server stays out of your way -- it just sets clients up in rooms and passes data between them (which they send and receive using `PRIV` and `DATA` messages, discussed below).

A basic usage pattern should probably look something like this:
* Wait for the server `HELLO`.
* Send you `HELLO`.
* Wait for the `WELCOME`.
* Send a `JOIN_GAME`.
* While True:
  * Watch the `ROOM_STATUS` messages.
  * Send and receive `DATA`/`PRIV` messages, possibly doing different things if you are the room leader.

Each message is sent over the connection as a newline-terminated JSON string, where the type has the key "TYPE"; other keys and their values are specific to the message type.  As a quick example, when a client wants to join a game of Tic-Tac-Toe, it might send the string `'{"TYPE": "HELLO", "name": "LizzyMagie", "gamename": "ttt"}\n'`.  There are libraries for interacting with JSON in just about every programming language today.


## Examples

Currently, there's only one example -- a terminal-based Tic-Tac-Toe game in Python.  Run it and pass the address of the server on the command line (e.g., `python3 ttt.py foo.example.com`).  The code is not beautiful or well documented or anything, but it does serve as a working example.


## Server to Client Messages

These messages are sent from the server to the client (sometimes in response to a client to server message with the same name).

### `HELLO`
The server sends this when a client first connects.

### `WELCOME`
Sent in response to a successful client `HELLO` message.

### `ERROR`
Sent when something bad has happened.
* `ERR`: An error code.
* `DESC`: A description (if any; may be missing).

### `PONG`
A response to a `PING`; it should include everything that was in the `PING`.


## Server to Client Room Messages
These are just server to client messages, but ones sent within a room should generally have the following attributes in addition to whatever message-specific ones they have:
* `YOU_LEAD`:  True if you’re the room leader.
* `SEQ`: A sequence number of messages sent by the room.  This is for use in auditing.

### `DATA`
This is data that was sent via the `DATA` client-to-server message.
* `ECHO`: True if you sent this message.
* `msg`: The message that was sent.

### `PRIV`
Similar to `DATA` but the message is only being sent to you, not everyone.

### `JOIN`
Sent when a user joins the room.
* `user`: The name of the user that joined the room.
* `leader`: True if this user is the room leader.
* `initial`: When you join a room, you get `JOIN` messages from all of the players in the room even if they were already in the room.  To differentiate these messages from joins that just happened, `initial` will be True.

### `LEAVE`
Sent when a user leaves the room.
* `user`: The name of the user that left.
* `leader`: True if this use was the room leader.

### `SPEC_JOIN`
Similar to JOIN except for spectators.

### `SPEC_LEAVE`
Similar to LEAVE but for spectators.

### `ROOM_STATUS`
Contains a mass of information about the room.
* `users`: List of players in the room.
* `spectators`: List of spectators in the room.
* `leader`: Name of room leader.
* `allow_spectators`: True if spectators are allowed in this room.
* `size`: The size of this room.
* `is_ready`: Whether the room is ready (has the right number of players).
* `was_ready`: Whether the room was ready the last time status was sent.
* `leaderstate`: See the `LEADERSTATE` client message.

### `CHOOSE`
The reply of the `CHOOSE` client message.
* `result`: The resulting choice.
* `opts`: The full list of options if the client specified `show`=True.
* `msg`: The message, if any, included by the client.

### `RANDINT`
The reply of a RANDINT client message; similar to `CHOOSE`.
* `result`: The resulting integer (or list, if count was specified).


## Client to Server

### `HELLO`
Introduce yourself to the server.  Must be done before most other stuff (in particular, before joining a room).  Usually sent in response to the `HELLO` server message.  May result in a `BADNAME` error if the name is not valid.
* `name`: The name you want to use.
* `gamename`: The name of the game type you're trying to play.

### `DATA`
Data sent with this client-to-server message is shared with others in the same room via the DATA server-to-client message.
* `target`: Can be "S", "P", or "SP".  Defaults to "SP".  Determines whether the message is sent to Spectators, Players, or both.
* `msg`: The message to send.

### `PRIV`
Similar to `DATA` but sends the message to a specific other user in the room.
* `user`: The name of the user to send to.

### `PING`
Elicits a `PONG` response containing all the same data from the server.  This is meant for checking that your connection to the server still works, and for preventing the server from disconnecting you due to idling.

### `SPECTATE_GAME`
Join a room as a spectator.  May fail with `NOGAMES` error if there are no games that can be spectated.

### `JOIN_GAME`
Join a room that matches your preferences as a player.  If there are no matching rooms, it will create one that fits your specifications.
* `size`: Can be an integer or a string like "3-10".  Specifies how big of a room (how many players) you want.  If a room is created, it will use the lower end of the range.
* `allow_spectators`: True if you want spectators to be able to join your game.

### `CHOOSE`
Selects a random thing from a list and sends it using the `CHOOSE` server message.
target: Like `DATA`, this can be S/P to specify who should see the resulting choice.
user: Like `PRIV`, this can specify a particular user to get the result.  If set, target is ignored.
* `opts`: The list of options.  If not specified, you'll get a random player.
* `show`: True to show the recipients the list of things being chosen from.
* `echo`: True to send the answer back to yourself too.
* `msg`: Optional message to receive

### `RANDINT`
Similar to CHOOSE but picks an integer (target/user/show/etc. work the same).  Note that you don't need to use this very much -- if you choose one `RANDINT`, you can use it to see a random number generator on each client.
* `lo`: The lower bound of numbers to pick.
* `hi`: The upper bound of numbers to pick.
* `count`: If specified, returns a list of integers instead of just one.

### `LEADERSTATE`
Sets state information which will be provided to the leader with `ROOM_STATUS` messages.  This is intended to allow the leader to store information so that if it dies, the next leader can recover the game.  Only the leader can execute this.
* `leaderstate`: The state to set (which will be included as `leaderstate` in subsequent `ROOM_STATUS` messages).


## Combating Cheating

If you're not worried about cheating, you can skips this section!

A potential danger of a generic server is that the server can't do any validation of client actions to make sure that the client isn't cheating (e.g., by using hacked client software).  A normal server is not a cure-all here either, of course, but it may be easier than with GGS.

In many cases, careful game design can ensure that clients stay honest or, at least, that you notice when they've cheated.  As an example, let's consider a board game where each player takes turn rolling dice to figure out how many spaces they can move their gamepiece.  If you left the dice roll entirely up to the client, a fairly simple hack would let a player choose whatever number they wanted instead of using a real random number, and they could likely easily win the game.

To combat this, the basic approach is that clients audit each other.  If you notice another client failing an audit, you abort the game.  There's thus no real incentive for them to try cheating, since everyone else will just leave if they do.

In our board game example, you can do this by having clients use the `RANDINT` message to have the server generate random numbers for the dice rolls.  This very generic functionality can be adapted to many situations.  For example, you can use a `RANDINT` to seed random number generators on the clients and then use those for other operations (e.g., shuffling).

In the case of dice in a board game, everyone can audit "in real time", as it's okay for everyone to see the dice when they're rolled.  In some games, however, various things need to be hidden and only revealed later.  For example, perhaps you have a game where at the start of each round, each player is supposed to write down the person they suspect of being the secret killer, but their guesses are supposed to be hidden until the end of the round.  They can't keep their guess entirely private, or a hacked client could let them change it during the round.  They also can't just send it to everyone at the start of the round because then a hacked client could let other users see the guess before the round ended.  What you need is a way to do an audit after the fact.

An older iteration of the generic game server idea had a couple of `escrow` features to support this type of case.  A client could tell the server a secret, and the server would reveal it to everyone later.  If you want this sort of feature in the new version, file an Issue (or implement it and file a Pull Request!).  However, you can pull off an okay version of it without any help from the server by using a bit of cryptography.  It works something like this:

* At the start of the round, a client has a `SECRET` (e.g., the name of the suspected killer).
* The client generates a `SALT` -- a random string of, say, 512 characters.
* The client appends the salt to the secret and then applies a cryptographic hash function to the result, yielding `HASH`.
* The client shares `HASH` with everyone else at the beginning of the round.

Other players can see `HASH`, but cryptographic hash / trap door functions, are hard to invert, so you can't easily go from `HASH` back to `SECRET`+`SALT`.  If the `SALT` weren't there, a player could just try applying the hash function themselves to each possible input (each player name in our example) and eventually they would get the `HASH` and know they had the correct input.  In our case, though, that doesn't work because of the large `SALT` value -- they don't have enough time to try every possible `SALT` value.

*However*, at the end of the round, the player can reveal their `SECRET` and their `SALT`, and other players can compute the hash themselves and see that it matches the `HASH` they were given at the start of the round.  If it doesn't, the player tried to change their guess.

In theory, the player could change their `SECRET` and find another `SALT` such that their concatenation will hash to the same value as the original `SECRET`+`SALT` did... but cryptographic hash functions make this difficult.
