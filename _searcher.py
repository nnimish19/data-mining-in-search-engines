#CHECK: url appear several times. make db query only for unique one

import re
import urllib2
import urllib
import webapp2
import cgi
import datetime
import logging
import itertools

from bs4 import *
from urlparse import urljoin

from google.appengine.ext import db
from google.appengine.api import users
from google.appengine.ext.db import stats

import webminingapp
import _nn
import stopwords         #List of words to ignore #Can use TF-IDF instead
import StemmerFile

#Global Variables
porter=StemmerFile.PorterStemmer()
    
class searcher:
    def query(self,q):
        rows,wordids=self.getmatchrows(q)       #All Results                    #rows=  [(url1,loc1,loc2..), (url1,loc1',loc2')]
        scores=self.getscoredlist(rows,wordids) #content/page_rank/nn           #scores={url1:score, url2:score)..}

        if scores==None or scores.values()==[]: return wordids,[]
            
        #Final
        rankedscores=sorted([(score,url) for (url,score) in scores.items( )],reverse=1)   #dictionary. reversed->descending order

        logging.info(scores)
        logging.info(rankedscores[0:10])
        #return wordids,[r[1] for r in rankedscores[0:10]],[r[0] for r in rankedscores[0:10]]   #Note this for training the NN. u hv-(1)wordids,urlids (2)get clicked link (3)call nn.trainquery()
        return wordids, rankedscores
            
    #def geturlname(self,id):
        #return webminingapp.Urllist.get_by_id(id).url     

    def separatewords(self,text):   
        splitter=re.compile('\\W*')     #\W* ->nonalpha(not a-zA-Z0-9)
        words= [s.lower( ) for s in splitter.split(text) if s!='']
        stemwords=[porter.stem(s, 0,len(s)-1) for s in words]
        return stemwords

#--------------------------------------------------------Searching---------------------------------------------------------
    def getmatchrows(self,q):               #in short reuturns those urls which contains all the query words and location of each word in that url. 
        #Strings to build the query         #If there are 2 words in q: rows=( (url0,loc1,loc2), (url1,loc1,loc2) ...  )

        #Split the words by spaces
        #words=q.split(' ')
        words=self.separatewords(q)
        
        wordids=[]
        for word in words:      #we are picking only those words which are in Datastore
            wordrow=webminingapp.Wordlist.all().filter('word',word).get()    #q.filter('word=',value)  #p=q.get()
            if wordrow!=None:
                wordids.append(wordrow.key().id())

        q=webminingapp.Wordlocation.all().filter('wordid IN', wordids)
        q.order('urlid')

        logging.info(words)
        logging.info(wordids)
        #logging.info(q)
        
        dic={}
        a=[[]] #1bucket for url rest for word location
        cnt=1
        for i in wordids:
            dic[i]=cnt
            a.append([])            #each bucket for a word location
            cnt=cnt+1              

        rows=[]
        prevID=-1
        for e in q:  #entity
            if e.urlid!=prevID:
                rows=rows+list(itertools.product(*a))  #if even 1 bucket is empty nothing is added to the rows
                a=[[] for x in a]       #empty a
                a[0].append(e.urlid)
                prevID=e.urlid
            a[dic[e.wordid]].append(e.location)

        rows=rows+list(itertools.product(*a))
        #logging.info(rows)
        return rows,wordids

#--------------------------------------------------------Ranking---------------------------------------------------------
    def getscoredlist(self,rows,wordids):
        totalscores=dict([(row[0],0) for row in rows])
        if totalscores.values()==[]:
            return 
        
        weights=[(1.0,self.locationscore(rows)),
                 (1.0,self.frequencyscore(rows)),
                 (1.0,self.pagerankscore(rows)),
                 (1.0,self.nnscore(rows,wordids))]
        
        for (weight,scores) in weights:
            for url in totalscores:
                totalscores[url]+=weight*scores[url]
        return totalscores

    def normalizescores(self,scores,smallIsBetter=0):
        vsmall=0.00001 #avoid division by zero errors
        if scores.values()==[]:
            return
        if smallIsBetter:
            minscore=min(scores.values())
            return dict([(u,float(minscore)/max(vsmall,l)) for (u,l) in scores.items()])
        else:
            maxscore=max(scores.values())
            if maxscore==0: maxscore=vsmall
            return dict([(u,float(c)/maxscore) for (u,c) in scores.items()])

#CONTENT BASED
    #Word Frequency     #no of occurance of urlids in Permutations(rows)
    def frequencyscore(self,rows):
        counts=dict([(row[0],0) for row in rows])
        for row in rows: counts[row[0]]+=1        
        return self.normalizescores(counts)
    #Document Location  #among all permutations the one with least sum of locations->for a single URL
    def locationscore(self,rows):
        locations=dict([(row[0],1000000) for row in rows])
        for row in rows:
            loc=sum(row[1:])
            if loc<locations[row[0]]: locations[row[0]]=loc
        return self.normalizescores(locations,smallIsBetter=1)
    #Word Distance     #absolute diff b/w consecutive locations ->for a single URL
    def distancescore(self,rows):
        #If there's only one word, everyone wins!
        if len(rows[0])<=2: return dict([(row[0],1.0) for row in rows])

        #Initialize the dictionary with large values
        mindistance=dict([(row[0],1000000) for row in rows])
        for row in rows:
            dist=sum([abs(row[i]-row[i-1]) for i in range(2,len(row))])
            if dist<mindistance[row[0]]: mindistance[row[0]]=dist
        return self.normalizescores(mindistance,smallIsBetter=1)

#USING inbound links
    def pagerankscore(self,rows):
        #Obtain unique urlids
        urlids=[row[0] for row in rows]
        urlids=list(set(urlids))
        pageranks=dict([(url,webminingapp.Pagerank.all().filter('urlid',url).get().score) for url in urlids])
        if pageranks.values()==[]:
            return
        maxrank=max(pageranks.values( ))
        normalizedscores=dict([(u,float(l)/maxrank) for (u,l) in pageranks.items()])
        return normalizedscores

#Using neural networks
    def nnscore(self,rows,wordids):
        #Obtain unique urlids
        urlids=[row[0] for row in rows]
        urlids=list(set(urlids))
        nnres=_nn.searchnet().getresult(wordids,urlids)                          #nn output in same order(only decimal numbers are returned)
        scores=dict([(urlids[i],nnres[i]) for i in range(len(urlids))])    #[(urlid,score), (,)] 
        return self.normalizescores(scores)  #{urlid: score), (,)}
