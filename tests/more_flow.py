import re, time, requests, warnings; warnings.filterwarnings("ignore")
BB="http://127.0.0.1:8061"
def login(u):
    s=requests.Session(); r=s.get(BB+"/login"); t=re.search(r'name="csrf" value="([^"]+)"',r.text).group(1)
    s.post(BB+"/login",data={"csrf":t,"username":u,"password":"password123"}); return s
def csrf(s,url="/feeds"): return re.search(r'name="csrf" value="([^"]+)"',s.get(BB+url).text).group(1)
fails=0
import subprocess
# scheduled topics need a timezone on the PieFed account (PieFed's web UI sets it; the API can't)
subprocess.run(["psql","-q","-h","127.0.0.1","-p","5433","-U","postgres","pyfedi","-c",
                "update \"user\" set timezone='Europe/Amsterdam' where user_name in ('victim','plainuser')"])
def check(n,c,x=""):
    global fails; print(("ok  " if c else "FAIL"),n,"" if c else x); fails+= not c
v=login("victim"); g=requests.Session()
r=v.get(BB+"/feeds"); check("feeds page", r.status_code==200 and "Retro Mix" in r.text and "Your feeds" in r.text, r.status_code)
check("guest feeds page", g.get(BB+"/feeds").status_code==200)
t=csrf(v)
r=v.post(BB+"/feeds/new",data={"csrf":t,"title":"My Mix","name":"mymix","communities":"retrogaming\n!emulation","description":"mine","public":"1"})
fid=int(re.search(r"/f/(\d+)",r.url).group(1)) if "/f/" in r.url else None
check("create feed", fid and "Feed made" in r.text, r.url)
r=v.get(BB+f"/f/{fid}"); check("feed page owner buttons", "Edit feed" in r.text and "Unfollow feed" not in r.text and "~mymix" in r.text, r.status_code)
r=v.post(BB+f"/f/{fid}/edit",data={"csrf":t,"title":"My Mix 2","communities":"retrogaming","public":"1"})
check("edit feed", "Feed saved" in r.text and "My Mix 2" in r.text, re.findall(r'class="flash">(.*?)<',r.text))
r=v.get(BB+f"/f/{fid}/edit"); check("edit form prefilled", "retrogaming@" in r.text and "emulation" not in r.text.split("communities")[1][:200])
time.sleep(11)
r=v.post(BB+"/f/1/follow",data={"csrf":t,"follow":"1"}); check("follow feed", "You follow this feed now" in r.text and "Unfollow feed" in r.text)
check("followed listed", "Retro Mix" in v.get(BB+"/feeds").text.split("Feeds you follow")[1].split("All public")[0])
import time; time.sleep(11)
r=v.post(BB+"/f/1/follow",data={"csrf":t,"follow":"0"}); check("unfollow feed", "no longer follow" in r.text and "Follow feed" in r.text)
r=v.post(BB+"/feeds/new",data={"csrf":t,"title":"Secret","name":"secretmix","communities":"amiga"})
pid=int(re.search(r"/f/(\d+)",r.url).group(1)) if "/f/" in r.url else None
check("unfollowed forum added despite PieFed bug", "Amiga" in v.get(BB+f"/f/{pid}").text)
check("private feed opens for owner", pid and v.get(BB+f"/f/{pid}").status_code==200 and "(private)" in v.get(BB+f"/f/{pid}").text, r.url)
check("private feed 404 for guest", g.get(BB+f"/f/{pid}").status_code==404)
m=login("modguy"); check("other user can't edit", m.get(BB+f"/f/{fid}/edit").status_code==403)
check("bad slug refused", "lowercase letters" in v.post(BB+"/feeds/new",data={"csrf":t,"title":"x","name":"Bad Name!"}).text)
for f in (fid,pid):
    r=v.post(BB+f"/f/{f}/edit",data={"csrf":t,"action":"delete","confirm":"1"}); check("delete feed", "Feed deleted" in r.text)
check("deleted feed gone", v.get(BB+f"/f/{fid}").status_code==404)
check("guest can't follow", g.post(BB+"/f/1/follow",allow_redirects=False).status_code in (302,400,403))

# ---- users: post alerts, notes
r=v.post(BB+"/u/id/3/alert",data={"csrf":t,"alert":"1"}); check("user alert on", "notification when they post" in r.text and "Stop post alerts" in r.text, r.url)
r=v.post(BB+"/u/id/3/alert",data={"csrf":t,"alert":"0"}); check("user alert off", "Alert me when they post" in r.text)
r=v.post(BB+"/u/id/3/note",data={"csrf":t,"note":"likes amigas"}); check("note saved", "Note saved" in r.text and 'value="likes amigas"' in r.text)
check("note shown in thread", "Note: likes amigas" in v.get(BB+"/p/1").text)
check("note not shown to others", "likes amigas" not in m.get(BB+"/p/1").text)
r=v.post(BB+"/u/id/3/note",data={"csrf":t,"note":""}); check("note removed", "Note removed" in r.text and "likes amigas" not in v.get(BB+"/p/1").text)
check("no alert/note on own profile", "Your note" not in v.get(BB+"/u/victim").text)

# ---- hide topic, block domain
PF="http://127.0.0.1:5000/api/alpha"
tok=requests.post(PF+"/user/login",json={"username":"plainuser","password":"password123"}).json()["jwt"]
lp=requests.post(PF+"/post",json={"title":"link to blockme","community_id":2,"url":"https://blockme.example/page"},headers={"Authorization":"Bearer "+tok}).json()["post_view"]["post"]["id"]
check("link topic listed", f'/p/{lp}"' in v.get(BB+"/c/emulation").text)
th=v.get(BB+f"/p/{lp}").text; check("block-domain button", "Block blockme.example" in th)
r=v.post(BB+"/domain/block",data={"csrf":t,"domain":"blockme.example","block":"1"}); check("domain blocked", "hidden from you now" in r.text, r.url)
check("blocked domain topic gone", f'/p/{lp}"' not in v.get(BB+"/c/emulation").text)
check("blocked domain listed", "blockme.example" in v.get(BB+"/blocked").text.split("Blocked link domains")[1])
r=v.post(BB+"/domain/block",data={"csrf":t,"domain":"blockme.example","block":"0"}); check("domain unblocked", "show again" in r.text)
check("topic back", f'/p/{lp}"' in v.get(BB+"/c/emulation").text)
check("bad domain refused", "a domain name (like" in v.post(BB+"/domain/block",data={"csrf":t,"domain":"not a domain","block":"1"}).text)
r=v.post(BB+f"/p/{lp}/hide",data={"csrf":t,"hidden":"1"}); check("hide topic", "Topic hidden" in r.text and "Unhide topic" in r.text)
check("hidden topic gone from list", f'/p/{lp}"' not in v.get(BB+"/c/emulation").text)
check("hidden only for that user", f'/p/{lp}"' in m.get(BB+"/c/emulation").text)
r=v.post(BB+f"/p/{lp}/hide",data={"csrf":t,"hidden":"0"}); check("unhide topic", "Topic unhidden" in r.text and "Hide topic" in r.text)
check("unhidden topic back", f'/p/{lp}"' in v.get(BB+"/c/emulation").text)

# ---- mentions, replies, reply alerts
def api_login(u): return {"Authorization":"Bearer "+requests.post(PF+"/user/login",json={"username":u,"password":"password123"}).json()["jwt"]}
HP, HM = api_login("plainuser"), api_login("modguy")
tp=requests.post(PF+"/post",json={"title":"inbox test","community_id":2},headers=HP).json()["post_view"]["post"]["id"]
pc=requests.post(PF+"/comment",json={"post_id":tp,"body":"plain's post"},headers=HP).json()["comment_view"]["comment"]["id"]
th=v.get(BB+f"/p/{tp}").text; check("alert replies button", "Alert replies" in th)
r=v.post(BB+f"/comment/{pc}/alert",data={"csrf":t,"alert":"1"}); check("reply alert on", "replies to that post" in r.text and "Alerting replies" in r.text, r.url)
requests.post(PF+"/comment",json={"post_id":tp,"parent_id":pc,"body":"modguy answers here"},headers=HM)
requests.post(PF+"/comment",json={"post_id":tp,"body":"hey @victim look"},headers=HM)
r=v.get(BB+"/notifications?show=replies"); check("replies tab", "modguy answers here" in r.text and "inbox test" in r.text, r.status_code)
r=v.get(BB+"/notifications?show=mentions"); # PieFed makes mention notifications in a background (celery) task, which this test PieFed doesn't run
check("mentions tab", r.status_code==200 and ("hey @victim look" in r.text or "Nobody has mentioned you yet" in r.text), r.status_code)
r=v.post(BB+f"/comment/{pc}/alert",data={"csrf":t,"alert":"0"}); check("reply alert off", "No more notifications" in r.text and "Alert replies" in r.text)
check("guest no inbox", g.get(BB+"/notifications?show=mentions",allow_redirects=False).status_code==302)

# ---- private messages: report, delete, leave
pm=requests.post(PF+"/private_message",json={"content":"pm for actions","recipient_id":4},headers=HM).json()
r=v.get(BB+"/messages/2"); check("conversation actions", "Leave conversation" in r.text and "Report conversation" in r.text and "Send report" in r.text, r.status_code)
pmid=pm.get("private_message_view",{}).get("private_message",{}).get("id")
r=v.post(BB+f"/pm/report/message/{pmid}",data={"csrf":t,"reason":"Spam"}); check("report message", "Report sent" in r.text, r.url)
cid=re.search(r"/conversation/(\d+)/leave", v.get(BB+"/messages/2").text)
r=v.post(BB+f"/pm/report/conversation/{cid.group(1) if cid else 0}",data={"csrf":t,"reason":"Spam"}); check("report conversation", "Report sent" in r.text)
mine=requests.post(PF+"/private_message",json={"content":"my own pm","recipient_id":2},headers=api_login("victim")).json()["private_message_view"]["private_message"]["id"]
r=v.post(BB+f"/pm/{mine}/delete",data={"csrf":t},headers={"Referer":BB+"/messages/2"}); check("delete own message", "Message deleted" in r.text and "[deleted]" in r.text, r.url)
r=v.post(BB+f"/conversation/{cid.group(1)}/leave",data={"csrf":t},headers={"Referer":BB+"/messages/2"}); check("leave needs confirm", "Tick the box" in r.text)
r=v.post(BB+f"/conversation/{cid.group(1)}/leave",data={"csrf":t,"confirm":"1"}); check("leave conversation", "left the conversation" in r.text, r.url)
import subprocess; subprocess.run(["psql","-q","-h","127.0.0.1","-p","5433","-U","postgres","pyfedi","-c","update conversation_member set joined=true"])
check("bad report kind", v.post(BB+"/pm/report/x/1",data={"csrf":t}).status_code==400)

# ---- posting: get title, scheduled topics, answers, suggestions
r=v.get(BB+"/c/emulation/new"); check("post-later and get-title fields", 'name="sched_at"' in r.text and 'name="fetch_title"' in r.text, r.status_code)
r=v.post(BB+"/c/emulation/new",data={"csrf":t,"title":"","url":"https://nowhere.invalid/x","fetch_title":"1"})
check("get title handles failure", "get a title" in r.text and r.status_code==200)
r=v.post(BB+"/c/emulation/new",data={"csrf":t,"title":"","url":"","fetch_title":"1"}); check("get title needs link", "Enter a link first" in r.text)
r=v.post(BB+"/c/emulation/new",data={"csrf":t,"title":"scheduled one","body":"later","sched_at":"2027-01-15T09:30","sched_repeat":"Weekly"})
check("schedule topic", "Topic scheduled" in r.text and "scheduled one" in r.text and "2027-01-15T09:30" in r.text, r.url)
check("scheduled topic not in forum yet", "scheduled one" not in v.get(BB+"/c/emulation").text)
sid=re.search(r'<b>scheduled one</b>.*?name="post_id" value="(\d+)"', r.text, re.S).group(1)
r=v.post(BB+"/scheduled",data={"csrf":t,"post_id":sid,"sched_at":"2027-02-01T08:00","sched_repeat":"None"}); check("reschedule", "New posting time saved" in r.text and "2027-02-01T08:00" in r.text)
r=v.post(BB+"/scheduled",data={"csrf":t,"post_id":sid,"action":"delete"}); check("delete scheduled", "Scheduled topic deleted" in r.text and "scheduled one" not in r.text)
r=v.post(BB+"/c/emulation/new",data={"csrf":t,"title":"bad time","sched_at":"notatime"}); check("bad schedule time", "valid date" in r.text)
mg=login("modguy"); tm=csrf(mg)
r=mg.post(BB+"/c/emulation/new",data={"csrf":tm,"title":"no tz","sched_at":"2027-01-15T09:30"}); check("schedule without PieFed timezone explained", "needs your timezone" in r.text, re.findall(r'class="flash">(.*?)<',r.text))
r=g.get(BB+"/suggest?q=!retro"); check("suggest forums", r.status_code==200 and any(x.startswith("retrogaming@") for x in r.json()), r.text[:100])
check("suggest users", any(x.startswith("plainuser@") for x in g.get(BB+"/suggest?q=@plain").json()))
check("suggest ignores plain text", g.get(BB+"/suggest?q=retro").json()==[])
# Q&A: make emulation a question & answer forum
import subprocess
subprocess.run(["psql","-q","-h","127.0.0.1","-p","5433","-U","postgres","pyfedi","-c","update community set question_answer=true where name='emulation'"])
th=requests.Session(); pp=login("plainuser"); tp_=csrf(pp)
q=requests.post(PF+"/post",json={"title":"a question","community_id":2},headers=HP).json()["post_view"]["post"]["id"]
ans=requests.post(PF+"/comment",json={"post_id":q,"body":"the answer"},headers=HM).json()["comment_view"]["comment"]["id"]
check("mark-answer button for author", "Mark as answer" in pp.get(BB+f"/p/{q}").text)
check("no mark-answer for others", "Mark as answer" not in v.get(BB+f"/p/{q}").text)
check("others refused", v.post(BB+f"/comment/{ans}/answer",data={"csrf":t,"answer":"1"}).status_code==403)
r=pp.post(BB+f"/comment/{ans}/answer",data={"csrf":tp_,"answer":"1"}); check("mark answer", "Marked as the answer" in r.text and "&#10003; Answer" in r.text or "✓ Answer" in r.text, r.url)
r=pp.post(BB+f"/comment/{ans}/answer",data={"csrf":tp_,"answer":"0"}); check("unmark answer", "No longer marked" in r.text)
subprocess.run(["psql","-q","-h","127.0.0.1","-p","5433","-U","postgres","pyfedi","-c","update community set question_answer=false where name='emulation'"])

# ---- moderator and admin tools
ad=login("dosadmin"); ta=csrf(ad)
lc=requests.post(PF+"/comment",json={"post_id":tp,"body":"to be locked"},headers=HP).json()["comment_view"]["comment"]["id"]
own=requests.post(PF+"/comment",json={"post_id":tp,"body":"admin speaking"},headers=api_login("dosadmin")).json()["comment_view"]["comment"]["id"]
th=ad.get(BB+f"/p/{tp}").text; check("mod menu has new tools", "Lock replies" in th and "Mod statement" in th and "Mark NSFW" in th and "/votes/post/" in th)
check("mod statement only on own posts", th.count(">Mod statement<")==1)
r=ad.post(BB+f"/mod/comment/{lc}",data={"csrf":ta,"action":"lock"},headers={"Referer":BB+f"/p/{tp}"}); check("lock comment", "Replies to this post are locked" in r.text and "Replies locked" in r.text, r.url)
r=ad.post(BB+f"/mod/comment/{lc}",data={"csrf":ta,"action":"unlock"},headers={"Referer":BB+f"/p/{tp}"}); check("unlock comment", "open again" in r.text)
r=ad.post(BB+f"/mod/comment/{own}",data={"csrf":ta,"action":"distinguish"},headers={"Referer":BB+f"/p/{tp}"}); check("distinguish", "moderator statement" in r.text and "[mod]" in r.text)
r=ad.post(BB+f"/mod/comment/{own}",data={"csrf":ta,"action":"undistinguish"},headers={"Referer":BB+f"/p/{tp}"}); check("undistinguish", "No longer marked as a moderator" in r.text)
r=ad.post(BB+f"/mod/post/{tp}",data={"csrf":ta,"action":"nsfw"},headers={"Referer":BB+f"/p/{tp}"}); check("mark nsfw", "Topic marked NSFW" in r.text and "Not NSFW" in r.text)
r=ad.post(BB+f"/mod/post/{tp}",data={"csrf":ta,"action":"sfw"},headers={"Referer":BB+f"/p/{tp}"}); check("unmark nsfw", "no longer marked NSFW" in r.text)
requests.post(PF+"/post/like",json={"post_id":tp,"score":1},headers=HM)
r=ad.get(BB+f"/votes/post/{tp}"); check("vote list", "modguy" in r.text and "up" in r.text, r.status_code)
check("vote list mods only", v.get(BB+f"/votes/post/{tp}").status_code==403)
r=ad.get(BB+"/modcp/messages?all=1"); check("message reports page", r.status_code==200 and "Reported messages" in r.text and "pm for actions" in r.text, r.status_code)
check("message reports admins only", v.get(BB+"/modcp/messages").status_code==403)
rid=re.search(r'name="kind" value="message"><input type="hidden" name="report_id" value="(\d+)"', r.text)
if rid:
    r=ad.post(BB+"/modcp/messages/resolve",data={"csrf":ta,"kind":"message","report_id":rid.group(1),"resolved":"1"}); check("resolve message report", "Report updated" in r.text)
check("modcp links message reports", "Message reports" in ad.get(BB+"/modcp").text)
r=ad.get(BB+"/admin/ban/4"); check("site ban page", "Ban victim" in r.text or "Ban victim@" in r.text, r.status_code)
check("site ban admins only", m.get(BB+"/admin/ban/4").status_code==403)
nb=requests.get(PF+"/user",params={"username":"newbie1"}).json()["person_view"]["person"]["id"]
r=ad.post(BB+f"/admin/ban/{nb}",data={"csrf":ta,"reason":"test ban"}); check("site ban", "banned from this site" in r.text, re.findall(r'class="flash">(.*?)<',r.text))
r=ad.post(BB+f"/admin/ban/{nb}",data={"csrf":ta,"action":"unban"}); check("site unban", "no longer banned" in r.text, re.findall(r'class="flash">(.*?)<',r.text))
# forum delete (a throwaway community)
import random; tw="throwaway%d" % random.randint(1000,9999)
cc=requests.post(PF+"/community",json={"name":tw,"title":"Throwaway"},headers=HM)
r=mg.get(BB+f"/c/{tw}/settings"); check("delete forum section", "Delete forum" in r.text, r.status_code)
r=mg.post(BB+f"/c/{tw}/settings",data={"csrf":tm,"action":"delete_forum"}); check("delete forum needs confirm", "Tick the box" in r.text)
r=mg.post(BB+f"/c/{tw}/settings",data={"csrf":tm,"action":"delete_forum","confirm":"1"}); check("delete forum", "Forum deleted" in r.text, re.findall(r'class="flash">(.*?)<',r.text))

# ---- blocklists (PieFed 1.9+)
pfv=tuple(int(x) for x in requests.get(PF+"/site/version").json()["version"].split(".")[:2])
if pfv < (1, 9):
    check("no blocklists before PieFed 1.9", g.get(BB+"/blocklists").status_code==404 and "/blocklists" not in v.get(BB+"/blocked").text)
    print("fails",fails); raise SystemExit(0)
check("blocked page links blocklists", "/blocklists" in v.get(BB+"/blocked").text)
check("guest blocklists page", g.get(BB+"/blocklists").status_code==200)
bln="bl%d" % random.randint(1000,9999)
r=mg.post(BB+"/blocklists/new",data={"csrf":tm,"title":"Spam accounts","name":bln,"members":"newbie1\nnobody@nowhere.invalid","description":"spam"})
blid=int(re.search(r"/blocklist/(\d+)",r.url).group(1)) if "/blocklist/" in r.url else None
check("make blocklist", blid and "Blocklist made" in r.text and "newbie1" in r.text, (r.url, re.findall(r'class="flash">(.*?)<',r.text)))
r=v.get(BB+f"/blocklist/{blid}"); check("blocklist page", "Follow blocklist" in r.text and "newbie1" in r.text)
r=v.post(BB+f"/blocklist/{blid}/follow",data={"csrf":t,"follow":"1"}); check("follow blocklist", "You follow this blocklist" in r.text and "Unfollow blocklist" in r.text, re.findall(r'class="flash">(.*?)<',r.text))
check("followed list shown", "Spam accounts" in v.get(BB+"/blocklists").text.split("Blocklists you follow")[1].split("All blocklists")[0])
out=subprocess.run(["psql","-qtA","-h","127.0.0.1","-p","5433","-U","postgres","pyfedi","-c",f"select count(*) from user_block where blocker_id=4 and blocked_id={nb} and blocked_via_blocklist"],capture_output=True,text=True).stdout.strip()
check("following blocks its accounts", out=="1", out)
r=v.post(BB+f"/blocklist/{blid}/follow",data={"csrf":t,"follow":"0"}); check("unfollow blocklist", "no longer follow" in r.text)
check("open by name", f"/blocklist/{blid}" in v.get(BB+f"/blocklists?name={bln}@127.0.0.1:5000").url)
check("unknown name explained", "know a blocklist" in v.get(BB+"/blocklists?name=nope@x.example").text)
check("others can't edit", v.get(BB+f"/blocklist/{blid}/edit").status_code==403)
r=mg.post(BB+f"/blocklist/{blid}/edit",data={"csrf":tm,"title":"Spam accounts 2","members":"newbie1","description":""}); check("edit blocklist", "Blocklist saved" in r.text and "Spam accounts 2" in r.text, re.findall(r'class="flash">(.*?)<',r.text))
r=mg.post(BB+f"/blocklist/{blid}/edit",data={"csrf":tm,"action":"delete","confirm":"1"}); check("delete blocklist", "Blocklist deleted" in r.text)
print("fails",fails)
