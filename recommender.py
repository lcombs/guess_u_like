#!/usr/bin/env python

#read in data
def data_read(spark, path):
    '''
    spark: spark
    file_path: 
        - interactions path: hdfs:/user/bm106/pub/goodreads/goodreads_interactions.csv
        - user_id path: hdfs:/user/bm106/pub/goodreads/user_id_map.csv
        - book_id path: hdfs:/user/bm106/pub/goodreads/book_id_map.csv
    which_csv: 'interactions', 'users', 'books'

    returns spark dataframe object with specified schema
    '''
    if path=='interactions':
        df=spark.read.csv('hdfs:/user/bm106/pub/goodreads/goodreads_interactions.csv', header = True, 
                                    schema = 'user_id INT, book_id INT, is_read INT, rating FLOAT, is_reviewed INT')
        return df
    elif path=='users':
        df=spark.read.csv('hdfs:/user/bm106/pub/goodreads/user_id_map.csv', header = True, 
                                    schema = 'user_id_csv INT, user_id STRING')
        return df
    elif path=='books':
        df=spark.read.csv('hdfs:/user/bm106/pub/goodreads/book_id_map.csv', header = True, 
                                    schema = 'book_id_csv INT, book_id STRING')
        return df
    
# Data splitting and subsampling
def data_prep(spark, spark_df, pq_path, fraction=0.01, seed=42, savepq=False, filter_num=10):
    '''
    spark: spark
    spark_df: spark object
    fraction: decimal percentage of users to retrieve (i.e. 0.01, 0.05, 0.25)
    seed: set random seed for reproducibility
    savepq: if we need to process the csv, prep the data and save parquet
    pq_path: save and/or read from path (i.e. 'hdfs:/user/eac721/onepct_int.parquet')

    returns records object with random, specified subset of users
    '''

    if savepq == True:

        # Recommender constraint: remove the users with only a low number of interactions

        import pyspark.sql.functions as f
        from pyspark.sql import Window

        w= Window.partitionBy('user_id')
        # Add a column with the number of interactions for all users 
        # note: we should rm this column using drop command
        spark_df=spark_df.select('user_id', 'book_id', 'is_read', 'rating', 'is_reviewed', f.count('user_id').over(w).alias('n_int')).sort('user_id')
        spark_df=spark_df.filter(spark_df.n_int>int(filter_num))
        #spark_df.show()
 
        # downsampling: sample a percentage of users, and take all of their interactions to make a miniature version of the data.
        users=spark_df.select('user_id').distinct()
        user_samp=users.sample(False, fraction=fraction, seed=seed)
        #user_samp.show()

        # inner join: keep only the randomly sampled users and all their interactions (based on percentage specified)
        records=spark_df.join(user_samp, ['user_id'])
        
        # check that this matches the desired percentage
        #print(records.select('user_id').distinct().count()) #--> 7711 
        #print(spark_df.select('user_id').distinct().count()) #--> 766717 

        # write to parquet format
        # note: this will fail if the path already exists - remove the file with "hadoop fs -rm -r onepct_int.parquet"
        records.orderBy('user_id').write.parquet(pq_path)

    records_pq = spark.read.parquet(pq_path)

    return records_pq

# train/val, test split (60/20/20 by user_id)
def train_val_test_split(spark, records_pq, seed=42):

    # number of distinct users for checking
    #print(records_pq.select('user_id').distinct().count())

    # Splitting procedure: 
    # Select 60% of users (and all of their interactions).
    # Select 20% of users to form the validation set (half interactions for training, half in validation). 
    # Select 20% of users to form the test set (same as validation).

    # find the unique users:
    users=records_pq.select('user_id').distinct()

    # sample the 60% and all interactions to form the training set and remaining set (test and val)
    users=records_pq.select('user_id').distinct()
    user_samp=users.sample(False, fraction=0.6, seed=seed)
    train=user_samp.join(records_pq, ['user_id'])
    train.show()
    print(train.select('user_id').distinct().count())
    test_val=records_pq.join(user_samp, ['user_id'], 'left_anti') 
    test_val.show()
    print(test_val.select('user_id').distinct().count())
    #print(train.select('user_id').distinct().count())
    #print(test_val.select('user_id').distinct().count())

    # split the remainder into test (20%), val (20%) - 50% split
    users=test_val.select('user_id').distinct()
    user_samp=users.sample(False, fraction=0.5, seed=seed)
    test=user_samp.join(test_val, ['user_id']) 
    val=test_val.join(user_samp, ['user_id'], 'left_anti')

    val.show()
    test.show()

    # split the validation set into 50/50 interactions
    val_train=val.sample(False, fraction=0.5, seed=seed)
    val=val.join(val_train, ['user_id', 'book_id', 'is_read', 'rating', 'is_reviewed'], 'left_anti')
    train=train.union(val_train)
    val.show()

    # same for test set
    test_train=test.sample(False, fraction=0.5, seed=seed)
    test=test.join(test_train, ['user_id', 'book_id', 'is_read', 'rating', 'is_reviewed'], 'left_anti')
    train=train.union(test_train)
    test.show()
    #import pandas as pd

    #temp=test.groupby('user_id').apply(lambda x: x.sample(frac=0.5)).reset_index(drop=True)
    #keys = list(temp.columns.values) 
    #i1 = test.set_index(keys).index
    #i2 = temp.set_index(keys).index
    #test_train = test[~i1.isin(i2)]
    #test = temp

    #temp=val.groupby('user_id').apply(lambda x: x.sample(frac=0.5)).reset_index(drop=True)
    #keys = list(temp.columns.values) 
    #i1 = val.set_index(keys).index
    #i2 = temp.set_index(keys).index
    #val_train = val[~i1.isin(i2)]
    #val = temp

    # add back to the training set
    #train=pd.concat([train, val_train, test_train], axis=0)

    # TO DO: remove items that are not observed in training from all three datasets
    
    #train=spark.createDataFrame(train, schema = 'user_id INT, book_id INT, is_read INT, rating FLOAT, is_reviewed INT')
    #val=spark.createDataFrame(val, schema = 'user_id INT, book_id INT, is_read INT, rating FLOAT, is_reviewed INT')
    #test=spark.createDataFrame(test, schema = 'user_id INT, book_id INT, is_read INT, rating FLOAT, is_reviewed INT')

    # check for each dataset to make sure the split works
    print(train.select('user_id').distinct().count())
    #print(test_val.select('user_id').distinct().count())

    print(val.select('user_id').distinct().count())
    print(test.select('user_id').distinct().count())

    #return train, val, test
    return train, test_val


### NEXT STEPS ###

# [x] (1) Convert to parquet and write files 
# [] (2) Convert wf from pandas to pyspark for train/val split
# [] (3) Any items not observed during training (i.e., which have no interactions in the training set, or in the observed portion of the validation and test users), can be omitted unless you're implementing cold-start recommendation as an extension.
# [x] (4) In general, users with few interactions (say, fewer than 10) may not provide sufficient data for evaluation, especially after partitioning their observations into train/test. You may discard these users from the experiment, but document your exact steps in the report.
        # DOCUMENT HERE - started by removing users with fewer than 10 interactions in the very beginning of the script
                        # note: this is a parameter we can tune later if we want

# [] (5) Implement basic recsys: pyspark.ml.recommendation module

# [] (6) Tune HP: rank, lambda

# [] (7) Evaluate - Evaluations should be based on predicted top 500 items for each user.
        # metrics: should we use AUC, avg. precicion, reciprocal rank?

# [] (8) Main 

#def main():

#import recommender
#interactions=recommender.data_read(spark, 'interactions')
#records=recommender.data_prep(spark, interactions, 'hdfs:/user/eac721/onepct_int.parquet', 0.01, 42, True, 10)
    ##records=recommender.data_prep(spark, interactions, 'hdfs:/user/eac721/onepct_int.parquet', 0.01, 42, False, 10)
#train, val, test = recommender.train_val_test_split(spark,records)

