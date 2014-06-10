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

from math import tanh
#from pysqlite2 import dbapi2 as sqlite

def dtanh(y):
    return 1.0-y*y

class Hiddennode(db.Model):
    create_key=db.StringProperty(indexed=True)
    
class Wordhidden(db.Model):
    fromid=db.IntegerProperty()
    toid=db.IntegerProperty()
    strength=db.FloatProperty()
    
class Hiddenurl(db.Model):
    fromid=db.IntegerProperty()
    toid=db.IntegerProperty()
    strength=db.FloatProperty()

class searchnet:    
    def getstrength(self,fromid,toid,layer):
        if layer==0: Table=Wordhidden 
        else: Table=Hiddenurl 

        res=Table.all().filter('fromid',fromid).filter('toid',toid).get()   #dont append .strength here bcz returned entity may be None type
        if res==None:
            if layer==0: return -0.2
            if layer==1: return 0
        return res.strength #[return res[0]

    def setstrength(self,fromid,toid,layer,strength):       #Note Weight matrix id is not being used anywhere. So update operation doesnt matter
        if layer==0: Table=Wordhidden #table='wordhidden'
        else: Table=Hiddenurl #table='hiddenurl'

        res=Table.all().filter('fromid',fromid).filter('toid',toid).get()
        if res==None:
            Table(fromid=fromid,toid=toid,strength=strength).put()
        else:
            res.strength=strength
            res.put()

#Key to functioning of NN. called during training
    def generatehiddennode(self,wordids,urls):   
        if len(wordids)>4:return
        # For pair in itertools.combinations(wordids,2):
        # Check if we already created a node for this set of words
        createkey='_'.join(sorted([str(wi) for wi in wordids]))
        res=Hiddennode.all(keys_only=True).filter('create_key',createkey).get()

        # If not, create it
        if res==None:            
            hid_node=Hiddennode(create_key=createkey).put()    #put() return 'key' object of entity
            hiddenid=hid_node.id()
        else:
            hiddenid=res.id()

        # Put in some default weights// If you put default weights everytime, its not gonna work
        for wordid in wordids:
            self.setstrength(wordid,hiddenid,0,1.0/len(wordids))    #self.setstrength(wordid,hiddenid,0,1.0/2)    #
        for urlid in urls:
            self.setstrength(hiddenid,urlid,1,0.1)
            

    def getallhiddenids(self,wordids,urlids):
        l1={}
        for wordid in wordids:
            q=Wordhidden.all().filter('fromid',wordid)
            for entity in q: l1[entity.toid]=1      #mapping ensures unique list
        
        for urlid in urlids:
            q=Hiddenurl.all().filter('toid',urlid)
            for entity in q: l1[entity.fromid]=1

        return l1.keys()    #list of unique ids

#----------------------------------------------------Get Result----------------------------------
    def setupnetwork(self,wordids,urlids):
        #value lists
        self.wordids=wordids
        self.hiddenids=self.getallhiddenids(wordids,urlids)
        self.urlids=urlids

        #node outputs
        self.ai = [1.0]*len(self.wordids)
        self.ah = [1.0]*len(self.hiddenids)
        self.ao = [1.0]*len(self.urlids)

        #create weights matrix
        self.wi = [[self.getstrength(wordid,hiddenid,0) for hiddenid in self.hiddenids] for wordid in self.wordids]
        self.wo = [[self.getstrength(hiddenid,urlid,1)  for urlid in self.urlids] for hiddenid in self.hiddenids]

    def feedforward(self):
        #the only inputs are the query words
        for i in range(len(self.wordids)):
            self.ai[i] = 1.0        #Output of each input layer node=1 bcz we have just considered those nodes which are from search terms
        #hidden layer output
        for j in range(len(self.hiddenids)):
            sum = 0.0
            for i in range(len(self.wordids)):
                sum = sum + self.ai[i] * self.wi[i][j]
                self.ah[j] = tanh(sum)
        #output layer output
        for k in range(len(self.urlids)):
            sum = 0.0
            for j in range(len(self.hiddenids)):
                sum = sum + self.ah[j] * self.wo[j][k]
                self.ao[k] = tanh(sum)
        return self.ao[:]

#Primary Function for nn results
    def getresult(self,wordids,urlids):                   
        self.setupnetwork(wordids,urlids)
        return self.feedforward()

#------------------------------------------------------Train NN--------------------------------
    def backPropagate(self, targets, alpha=0.5):    #alpha: learning rate[0-1]. 
        # calculate errors for output
        output_deltas = [0.0] * len(self.urlids)
        for k in range(len(self.urlids)):
            error = targets[k]-self.ao[k]                   #error: how much output needs to be changed for this node
            output_deltas[k] = dtanh(self.ao[k]) * error    #delta= outputFn_derivative_wrt_output *error = #error: how much input needs to be changed for this node
        
        # calculate errors for hidden layer
        hidden_deltas = [0.0] * len(self.hiddenids)
        for j in range(len(self.hiddenids)):
            error = 0.0
            for k in range(len(self.urlids)):
                error = error + output_deltas[k]*self.wo[j][k]
            hidden_deltas[j] = dtanh(self.ah[j]) * error

        #IDEA
        #cost function, J(theta) = Mean squared error = 1/n *summation( (true_output - observed_ouput)^2) for a given node
        #theta = input vector
        #true_output = dependent on next level node's delta (for Hidden layer)
        #observed_output = tanh(theta)
            
        #using Gradient descent
        #theta= theta + alpha * derivative (J(theta)) wrt theta
        #This expression reduces to
        #theta= theta + alpha * delta *observed_output
        #theta[i][j]= theta[i][j] + alpha * delta[j]* O[i]   for all j connected to i
            
        #i: ith node of current level. j: jth node of next level
        #O[i]= ith node output. delta[j]: jth node error
        
        
        #update output weights        
        for j in range(len(self.hiddenids)):
            for k in range(len(self.urlids)):
                change = output_deltas[k]*self.ah[j]
                self.wo[j][k] = self.wo[j][k] + alpha*change   

        #update input weights        
        for i in range(len(self.wordids)):              
            for j in range(len(self.hiddenids)):        
                change = hidden_deltas[j]*self.ai[i]    
                self.wi[i][j] = self.wi[i][j] + alpha*change    

# Primary Function for Learning
    def trainquery(self,wordids,urlids,selectedurl):        
        # generate a hidden node if necessary
        logging.info('=======TrainQuery======')
        logging.info(wordids)
        logging.info(urlids)
        self.generatehiddennode(wordids,urlids)#KEY to functioning of whole NN
        self.setupnetwork(wordids,urlids)
        self.feedforward()
        
        targets=[0.0]*len(urlids)
        targets[urlids.index(selectedurl)]=1.0
        
        error = self.backPropagate(targets)
        self.updatedatabase()

    def updatedatabase(self):
        # set them to database values
        for i in range(len(self.wordids)):
            for j in range(len(self.hiddenids)):
                self.setstrength(self.wordids[i],self. hiddenids[j],0,self.wi[i][j])

        for j in range(len(self.hiddenids)):
            for k in range(len(self.urlids)):
                self.setstrength(self.hiddenids[j],self.urlids[k],1,self.wo[j][k])
