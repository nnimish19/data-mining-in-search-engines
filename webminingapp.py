import re
import urllib2
import urllib
import webapp2
import cgi
import datetime
import logging

from bs4 import *
from urlparse import urljoin

from google.appengine.ext import db
from google.appengine.api import users
from google.appengine.ext.db import stats


import _searcher
import _nn
import stopwords         #List of words to ignore #Can use TF-IDF instead
import StemmerFile
import HTML

# Global Variables
mys=_searcher.searcher()
myn=_nn.searchnet()

wordids=[]
urlids=[]
mycheck=0
porter=StemmerFile.PorterStemmer()

#[1]CRAWLING and BUILDING INDEX-----------------------------------------------------------------------

class Urllist(db.Model):
    url = db.StringProperty(indexed=True)
    title = db.StringProperty(multiline=True)
    description = db.StringProperty(multiline=True)
    date = db.DateTimeProperty(auto_now_add=True)    
    
class Wordlist(db.Model):
    word = db.StringProperty(indexed=True)
    
class Wordlocation(db.Model):
    urlid=db.IntegerProperty()
    wordid = db.IntegerProperty(indexed=True)
    location=db.IntegerProperty()
    
class Link(db.Model):
    fromid = db.IntegerProperty(indexed=True)
    toid = db.IntegerProperty(indexed=True)
    
class Linkwords(db.Model):
    wordid = db.IntegerProperty()
    linkid = db.IntegerProperty()

class Pagerank(db.Model):
    urlid=db.IntegerProperty(indexed=True)
    score=db.FloatProperty()

#---------------------------------------------------------------------#---------------------------------------------------------------------
class crawler:
#Auxilary Funtions
    def separatewords(self,text):   
        splitter=re.compile('\\W*')     #\W* ->nonalpha(not a-zA-Z0-9)
        words= [s.lower( ) for s in splitter.split(text) if s!='']
        stemwords=[porter.stem(s, 0,len(s)-1) for s in words]
        return [s for s in stemwords if s not in stopwords.ignorewords]
    
    def isindexed(self,url):    #url = string
        p1=db.GqlQuery("Select __key__ from Urllist where url=:1" , url).get()  #<- return 1 entity_key if any
        if p1!=None:
            p2=db.GqlQuery("Select __key__ from Wordlocation where urlid=:1" , p1.id()).get()
            if p2!=None:return True
        return False
    
    def updateUrllist(self,url,soup,text):
        p=Urllist.all().filter('url',url).get()
        if p==None: p=Urllist(url=url)

        if soup.title!=None: tit=soup.title.string
        else:
            tit=text[0:max(50,len(text)-1)]+'...'       #tit=re.sub(' +|\n|\r|\t', ' ', tit)            
            tit=' '.join(tit.split())                   #tit=tit.replace('\n','').replace('\t','').replace('\r','')

        des='' 
        meta_tags=soup('meta')
        for mtag in meta_tags:
            if ('name' in dict(mtag.attrs)):
                if mtag['name']=='description':
                    if ('content' in dict(mtag.attrs)):
                        des= mtag['content']   #logging.info(mtag['content'])
                        break
        if des=='':
            des=text[0:max(200,len(text)-1)]+'...'
            des=' '.join(des.split())                   #des = des.rstrip('\n') OR des=re.sub(' +|\n|\r|\t', ' ', des)
            
        p.title=tit
        p.description=des
        p.put()
        return p.key().id()
        

    #If url not there, add it to the DB and return its id. only 1st url would be added this way. all others have already been added during linking
    def getentryidUrllist(self,value):  
        q=Urllist.all()
        q.filter('url',value)
        p=q.get()
        if p==None:
            entity=Urllist(url=value)
            entity.put()
            return entity.key().id()
        else:
            return p.key().id()
        
    def getentryidWordlist(self,value):
        q=Wordlist.all()
        q.filter('word',value)
        p=q.get()
        if p==None:
            entity=Wordlist(word=value)
            entity.put()
            return entity.key().id()
        else:
            return p.key().id()
                
#---------------------------------------------------------------------#---------------------------------------------------------------------
#DEFINING FUNCTION
    def crawl(self,pages,depth=1): #default depth 1
        for i in range(depth):
            #print 'iteration %d' %i
            newpages=set( )
            for page in pages:
            #{
                if page[0:5]=='http:': page='https:'+page[5:]
                if self.isindexed(page): return
                
                try:
                    c=urllib.urlopen(page)
                    soup=BeautifulSoup(c.read( ))
                    c.close()
                except:
                    print "Could not open %s" % page
                    continue

                #file = open('pg source.txt', 'r')
                #logging.info("----------SOUP TEST------------")
                #logging.info(soup.get_text())
                #logging.info(soup.title.string)
    
                #add page to index
                self.addtoindex(page,soup)
    
                #collect links on page                       
                links=soup('a')     #[<a href="/guides/">Tutorials</a>, <a href="/guides1/" font="roman">Tutorials1</a>]
                for link in links:                           #link: <a href="/guides/">Tutorials</a>
                    if ('href' in dict(link.attrs)):         #link.attrs =>{'href': '/guides/', 'font': 'roman' }
                        url=urljoin(page,link['href'])       #https://help.websiteos.com/  +  /guides/  =  https://help.websiteos.com/guides/
                        if url.find("'")!=-1: continue       #NOTE: If href="http://www.somewebsite" then url=http://www.somewebsite. see behaviour at link given above
                        url=url.split('#')[0]   # remove location portion(internal hyperlinks)
                        if url[0:6]=='https:' or url[0:5]=='http:':  #if url[0:20]=='http://www.imdb.com/':    # last verification
                            if url[0:5]=='http:': url='https:'+url[5:]
                            if not self.isindexed(url) and (url not in newpages):
                                newpages.add(url)                        
                            
                            linkText=link.string     #linkText=self.gettextonly(link)
                            self.addlinkref(page,url,linkText)      #urlid=self.getentryid('urllist','url',url)   #it may be costly adding it here bcz, we would be repeatedly querying from db
    
                #self.dbcommit()
            #}
            pages=newpages
        self.calculatepagerank()

#Prepare tables: Urllist(url,title,description), Wordlist(word), Wordlocation(wordid,urlid,location)
    def addtoindex(self,url,soup): 
        if self.isindexed(url): return
        logging.info('Indexing '+url)
        
        text=soup.get_text()

        #update Urllist: add Title and description
        urlid=self.updateUrllist(url,soup,text)

        #Get words
        words=self.separatewords(text)  #split, convert lower_case, stemmer, stopwords
        
        #update Wordlist and Wordlocation tables
        for i in range(len(words)):
            word=words[i]
            wordid=self.getentryidWordlist(word)                          
            Wordlocation(urlid=urlid, wordid=wordid, location=i).put()

#Prepare tables: Link and Linkwords
    def addlinkref(self,from_add,to_add,linkText):
        #add to link table(fromid,toid)
        fid=self.getentryidUrllist(from_add)
        tid=self.getentryidUrllist(to_add)  
        
        entity_key=Link(fromid=fid,toid=tid).put() 
        linkid=entity_key.id()                      

        #add to linkword table(wordid,linkid)
        words=self.separatewords(linkText)          #split, convert lower_case, stemmer, stopwords
        for i in range(len(words)):
            word=words[i]
            wordid=self.getentryidWordlist(word)          
            Linkwords(wordid=wordid,linkid=linkid).put() 

#Prepare table: Pagerank                                         
    def calculatepagerank(self,iterations=5):
        #clear out the current PageRank tables 
        db.delete(Pagerank.all(keys_only=True))
                
        #initialize every url with a PageRank of 1 
        q1=Urllist().all(keys_only=True)
        for e in q1:
            Pagerank(key_name=str(e.id()), urlid=e.id(), score=1.0).put()     #key_name not necessary   

        logging.info('-------Pagerank-------')
        dic={}
        for i in range(iterations):
            #logging.info('iteration ='+str(i))
            for url_entity in q1:       
                pr=0.15
                
                #logging.info('url ='+str(url_entity.id()))
                # Loop through all the pages that link to this one
                q2=Link.all().filter('toid',url_entity.id())
                for linker in q2:                
                    #get PageRank of the linker
                    linkingpr=Pagerank.all().filter('urlid', linker.fromid).get().score
                    #get total number of links from the linker                    
                    linkingcount=Link.all(keys_only=True).filter('fromid',linker.fromid).count()
                    pr+=0.85*(linkingpr/linkingcount)

                    #logging.info(str(linker.fromid)+" "+str(linkingpr))
                    
                dic[url_entity.id()]=pr
                #logging.info(str(url_entity.id())+str(pr))
                
            #self.dbcommit()
            for url_entity in q1:
                entity=Pagerank.all().filter('urlid',url_entity.id()).get()
                entity.score=dic[url_entity.id()]
                entity.put()
                

#---------------------------------------------------------------------#---------------------------------------------------------------------
class MainPage(webapp2.RequestHandler):
    def get(self):
        self.response.out.write(HTML.INDEX)
        global mycheck
        if mycheck==0:
            mycheck=1
            logging.info('======Crawler======')
            seed='https://www.udacity.com/cs101x/index.html'
            intdepth=3
            crawler().crawl([seed],intdepth)

class Guestbook(webapp2.RequestHandler):        #pg= '/sign': loaded whenever this pg is called (form action on homepage)
    def post(self):
        global wordids
        global urlids
        #seed=cgi.escape(self.request.get('url_name'))
        strquery=cgi.escape(self.request.get("query_"))
        #depth=cgi.escape(self.request.get('depth_name'))
        #intdepth=int(float(depth))    
        #if intdepth>4:
            #self.response.out.write('<html><body bgcolor="white">Keep depth <5!\n</body></html>')
            #return
        logging.info(strquery)

        wordids=[]
        urlids=[]
        #Search
        logging.info('======Searcher======')
        wordids,rankedscores=mys.query(strquery)
        logging.info(strquery)
        logging.info(wordids)
        logging.info(rankedscores)
    
        logging.info('======Print result======')
        self.response.out.write(HTML.SIGN)
        
        if rankedscores!=[]:
            for j in range(len(rankedscores)):#for i in urlids:
                i=rankedscores[j]                         #(score, url)
                self.response.out.write('['+str(i[0])+']<br>')

                urlids.append(i[1])
                url_entity=Urllist.get_by_id(i[1])        #OR url=Urllist.all().filter('id', i).get() -> if you define your field id
                if url_entity.title!=None:
                    self.response.out.write('Title: '+'<a target="_blank" id="w3s" class="testClick" href="/redir_url?' +url_entity.url+ '">' +url_entity.title+'</a> <br>')      #<a href="url">Link text</a>
                    self.response.out.write('URL: '+url_entity.url+'<br>Description: '+url_entity.description+'<br><br>')
                else:
                    self.response.out.write('URL: '+'<a target="_blank" id="w3s" class="testClick" href="/redir_url?' +url_entity.url+ '">' +url_entity.url+'</a> <br>')
                    
            
        else:
            self.response.out.write('No results found!\n')
        #self.response.out.write(index[key])
        self.response.out.write('</pre></body></html>')
    

class RedirUrl(webapp2.RequestHandler):             #pg= '/redir_url?...': loaded whenever this type of url is clicked. 
    def get(self):
        selectedurl = self.request.query_string
        logging.info('user clicked ' + selectedurl)
        myn.trainquery(wordids,urlids,Urllist.all(keys_only=True).filter('url',selectedurl).get().id())
        self.redirect(selectedurl)
        
        #test=name
        #element=Critic4(name=url)
        #element.put()
        #logging.info(Critic4.properties())

app1 = webapp2.WSGIApplication([('/', MainPage),('/sign',Guestbook)],
                              debug=True)
app2 = webapp2.WSGIApplication([('/', MainPage),('/redir_url?',RedirUrl)],
                              debug=True)

#---------------------------------------------------------------------#---------------------------------------------------------------------

#db.delete(Urllist.all(keys_only=True))
#db.delete(Wordlist.all(keys_only=True))
#db.delete(Wordlocation.all(keys_only=True))
#db.delete(Link.all(keys_only=True))
#db.delete(Linkwords.all(keys_only=True))
#db.delete(Pagerank.all(keys_only=True))

#db.delete(_nn.Hiddennode.all(keys_only=True))
#db.delete(_nn.Wordhidden.all(keys_only=True))
#db.delete(_nn.Hiddenurl.all(keys_only=True))
