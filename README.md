# minecraft server in github codespaces

technically you can run this on your own pc if your using linux because it just works, but this was intended for codespaces

fork this repo and run `install.sh` to set up a vanilla minecraft java server, with java and a playit tunnel included.

the hosting setup stays in your browser. you still play using minecraft java on your computer.

## 1. open it in codespaces

well first you gotta open it in codespaces (kinda the whole point)

on your fork, click **code → codespaces → create codespace**, then open the terminal.

run the install commands from the repo's top folder, where `install.sh` is. if you're already inside `minecraft-server/`, run `cd ..` first.

if you're updating an existing codespace, run `git pull` first.

## 2. install the server

to use the latest stable release, run:

```bash
bash install.sh
```

to choose a specific version, run this instead (1.21.1 is just an example):

```bash
bash install.sh 1.21.1
```

the installer creates `minecraft-server/` beside `install.sh`. it downloads the official vanilla server, the java version it needs, and playit.

rerunning it keeps your installed minecraft version and existing settings. it won't automatically upgrade your world or replace a modified server jar. use a separate server folder if you want a different version, and back up your world first.

## 3. accept the eula

read the [minecraft eula](https://aka.ms/MinecraftEULA). or don't. i didn't.

to agree (even if u dont), open `minecraft-server/eula.txt` in the editor, change `eula=false` to `eula=true`, and save it.

the installer doesn't accept it for you, i think thats illegal.

## 4. start minecraft and the tunnel

from the repo's top folder:

```bash
cd minecraft-server/
bash start-public.sh
```

if you're already in `minecraft-server/`, just run `bash start-public.sh`.

this starts minecraft and playit together. don't also run `start.sh` or another copy of the launcher at the same time.

on the first run, playit prints a claim link. open that link in your browser, sign in or create a playit account, and claim the agent. keep the terminal running while you do this.

## 5. set up playit

in the playit dashboard, add a tunnel using the agent you just claimed.

| setting | what to put |
| --- | --- |
| tunnel type | minecraft java |
| tunnel name | anything you want, it doesn't matter |
| local ip / local address | `127.0.0.1` |
| local port | `25565` |
| proxy protocol | none / disabled |

yep, the local ip really is `127.0.0.1`. dont use that ip to join, if you know about websites you know that this is just localhost. playit and minecraft are running inside the same codespace, so it points playit at the minecraft server there. you cant use 127.0.0.1 but it can because they are running "in the same computer".

leave **proxy protocol off** for this vanilla setup. don't pick proxy protocol v1 or v2, and you don't need to install a separate minecraft proxy. playit already handles the public tunnel.

if you changed `server-port` in `server.properties`, use that port as the local port instead.

save the tunnel. check that the agent is online and the tunnel is enabled.

## 6. actually join the server

wait for minecraft to print its `Done` message, then copy the **public server address playit gives you** from the tunnel page.

open minecraft **java edition**, using the same minecraft version as the server. go to **multiplayer → add server** or **direct connection**, and paste that address.

if playit gives you an address with a port, copy the whole thing, including `:port`. the public port can be different from the local port.

your friends use that same public address. don't give them `127.0.0.1`, the codespace's internal ip, or its `app.github.dev` browser preview url.

the server uses `online-mode=true`, so players need a valid minecraft java account.

> if your trying to run a command and it gets cut off by the playit messages, don't retype it, it's still "typed"

## stopping and starting again

type this in the running server terminal:

```text
stop
```

let it finish saving. the public launcher also stops its playit process when minecraft exits.

don't use ctrl-c, that might break it.

next time, start the codespace, open the terminal, and run this from the repo's top folder:

```bash
cd minecraft-server/
bash start-public.sh
```

you normally only claim the agent and create the tunnel once. keep `minecraft-server/.playit/playit.toml` private; it stores the agent credentials.

## if it won't connect

- no claim link? run `bash tunnel.sh` in a separate terminal inside `minecraft-server/` to do the playit setup on its own. stop it with ctrl+c before using `start-public.sh` again.
- connection refused? check minecraft reached `Done`, the agent is online, and the tunnel points to `127.0.0.1:25565`.
- version mismatch? check `minecraft-server/minecraft-version.txt` and use that version in your minecraft launcher.
- disconnects or protocol errors? double check proxy protocol is disabled.
- `install.sh: no such file or directory`? you're probably inside the server folder. run `cd ..` to get back to the repo's top folder.
- need to reduce memory? run `MC_MEMORY=1536M bash start-public.sh` inside `minecraft-server/`.

## keeping your world

the codespace has to stay running for anyone to join. suspension stops the server and tunnel; this setup doesn't make codespaces a 24/7 host.

back up your worlds somewhere outside the codespace before deleting it. generated server files are ignored by git, so pushing the repo doesn't back them up.

the installer has had syntax and mocked setup checks, but a live codespaces server + playit connection hasn't been verified here.

playit's own guides: [minecraft setup](https://playit.gg/support/how-to-setup-a-mc-server/) and [proxy protocol setup for paper](https://playit.gg/support/proxy-protocol-papermc/) (that second one is for paper, this repo uses vanilla).
