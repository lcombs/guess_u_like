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
    spark_df: spark df, output from date_read
    fraction: decimal (%) of users to retrieve (i.e. 0.01, 0.05, 0.25)
    seed: set random seed for reproducibility
    savepq: whether we need to process csv file; if True, prep the data and save parquet
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
        spark_df=spark_df.drop('n_int')#.collect() # needs to be tested
        #spark_df.show()
 
        # downsampling: sample a percentage of users, and take all of their interactions to make a miniature version of the data.
        users=spark_df.select('user_id').distinct()
        user_samp=users.sample(False, fraction=fraction, seed=seed)
        #user_samp.show()

        # inner join: keep only the randomly sampled users and all their interactions (based on percentage specified)
        records=spark_df.join(user_samp, ['user_id'])
        
        # check that this matches the desired percentage
        #print(records.select('user_id').distinct().count()) 
        #print(spark_df.select('user_id').distinct().count())  

        # write to parquet format
        # note: this will fail if the path already exists 
        # remove the file with "hadoop fs -rm -r onepct_int.parquet"
        records.orderBy('user_id').write.parquet(pq_path)

    records_pq = spark.read.parquet(pq_path)

    return records_pq

def train_val_test_split(spark, records_pq, seed=42):

    '''
    # This function takes the following splitting procedure: 
    # Select 60% of users (and all of their interactions).
    # Select 20% of users to form the validation set (half interactions for training, half in validation). 
    # Select 20% of users to form the test set (same as validation).

    spark: spark
    records_pq: spark df output from data_prep function
    seed: random seed

    returns train, val, test data
    '''

    # number of distinct users for checking
    #print(records_pq.select('user_id').distinct().count())

    # find the unique users:
    users=records_pq.select('user_id').distinct()
    #print(users.count())

    # sample the 60% and all interactions to form the training set and remaining set (test and val)
    users=records_pq.select('user_id').distinct()
    user_samp=users.sample(False, fraction=0.6, seed=seed)
    train=user_samp.join(records_pq, ['user_id'])
    test_val=records_pq.join(user_samp, ['user_id'], 'left_anti') 
    #print(train.select('user_id').distinct().count())
    #print(test_val.select('user_id').distinct().count())

    # split the remaining set into 50/50 by users' interactions
    #print(test_val.groupBy('user_id').count().orderBy('user_id').show())
    users2=test_val.select('user_id').distinct().collect()
    frac = dict((u.user_id, 0.5) for u in users2)
    #print(frac)
    test_val_train=test_val.sampleBy('user_id', fractions=frac, seed=seed)
    test_val=test_val.join(test_val_train, ['user_id', 'book_id'], 'left_anti') 
    #print(test_val.groupBy('user_id').count().orderBy('user_id').show())
    # add training 50% back to train
    train=train.union(test_val_train) 
    #print(train.select('user_id').distinct().count())
    #print(test_val.select('user_id').distinct().count())

   # split the test_val set into test (20%), val (20%) by user
    users3=test_val.select('user_id').distinct()
    user_samp=users3.sample(False, fraction=0.5, seed=seed)
    test=user_samp.join(test_val, ['user_id']) 
    val=test_val.join(user_samp, ['user_id'], 'left_anti')
    #print(val.select('user_id').distinct().count())
    #print(test.select('user_id').distinct().count())

    # remove items that are not in training from all three datasets
    # find items in val and/or test that are not in train
    itemsv=val.select('book_id').distinct()
    itemst=test.select('book_id').distinct()
    items_testval=itemsv.union(itemst).distinct()

    items_train=train.select('book_id').distinct()
    #print(items_train.orderBy('book_id').show())
    items_rm=items_testval.join(items_train, ['book_id'], 'leftanti')
    #print(items_rm.orderBy('book_id').show())

    #print(train.groupBy('book_id').count().orderBy('book_id').show())
    #print(val.groupBy('book_id').count().orderBy('book_id').show())
    #print(test.groupBy('book_id').count().orderBy('book_id').show())
    train=train.join(items_rm, ['book_id'], 'left_anti')
    val=val.join(items_rm, ['book_id'], 'left_anti')
    test=test.join(items_rm, ['book_id'], 'left_anti')
    #print(train.groupBy('book_id').count().orderBy('book_id').show())
    #print(val.groupBy('book_id').count().orderBy('book_id').show())
    #print(test.groupBy('book_id').count().orderBy('book_id').show())
    
    # check for each dataset to make sure the split works
    #print(train.select('user_id').distinct().count())
    #print(val.select('user_id').distinct().count())
    #print(test.select('user_id').distinct().count())

    return train, val, test

def recsys_fit(train, val, test):

    '''
    This function fits the recommender system.
    
    train: Input training data to fit the model
    val: Input validation data to tune the model

    References:
    # https://spark.apache.org/docs/latest/api/python/pyspark.ml.html#module-pyspark.ml.recommendation
    # https://www.kaggle.com/vchulski/tutorial-collaborative-filtering-with-pyspark
    # https://spark.apache.org/docs/2.2.0/ml-collaborative-filtering.html
    
    returns the ALS model object
    '''

    from pyspark.ml.recommendation import ALS, ALSModel
    from pyspark.ml.evaluation import RegressionEvaluator
    from pyspark.ml.tuning import CrossValidator, ParamGridBuilder
    from pyspark.sql import functions as f


    # subset the data
    train = train.select("user_id","book_id","rating")
    val = val.select("user_id","book_id","rating")
    test = test.select("user_id","book_id","rating")

    # Build the recommendation model using ALS on the training data
    als = ALS(maxIter=10, regParam=0.01, 
          userCol="user_id", itemCol="book_id", ratingCol="rating",
          coldStartStrategy="drop",
          implicitPrefs=False)
    model = als.fit(train)

    #baseline evaluation on validation data:
    predictions = model.transform(val)

    evaluator = RegressionEvaluator(metricName="rmse", labelCol="rating", predictionCol="prediction")
    rmse = evaluator.evaluate(predictions)
    # evaluate the baseline model on the val set
    print("The baseline model was trained with rank = %d " % (model.rank) + "and its RMSE on the validation set is %f." % (rmse))

    # hyperparameter tuning: grid serach for rank, lambda using validation set, 5 fold CV
    # find a better way to tune: 
    # https://databricks-prod-cloudfront.cloud.databricks.com/public/4027ec902e239c93eaaa8714f173bcfc/3175648861028866/48824497172554/657465297935335/latest.html

    #paramGrid = ParamGridBuilder().addGrid(model.rank, [10, 100, 1000]).build()

    #crossval = CrossValidator(estimator=model,estimatorParamMaps=paramGrid,evaluator=RegressionEvaluator(),numFolds=5)  
    #cvmodel = crossval.fit(val)
    #best_model = model.bestModel 
    # NOTE: need to improve evaluation metrics

    tolerance = 0.03
    ranks = [4, 8, 12, 16]
    regParams = [0.15, 0.2, 0.25]
    errors = [[0]*len(ranks)]*len(regParams)
    models = [[0]*len(ranks)]*len(regParams)
    err = 0
    min_error = float('inf')
    best_rank = -1
    i = 0
    for regParam in regParams:
      j = 0
      for rank in ranks:
        # Set the rank here:
        als.setParams(rank = rank, regParam = regParam)
        # Create the model with these parameters.
        model = als.fit(train)
        # Run the model to create a prediction. Predict against the validation_df.
        predict_df = model.transform(val)

        # Remove NaN values from prediction (due to SPARK-14489)
        predicted_plays_df = predict_df.filter(predict_df.prediction != float('nan'))
        predicted_plays_df = predicted_plays_df.withColumn("prediction", f.abs(f.round(predicted_plays_df["prediction"],0)))
        # Run the previously created RMSE evaluator, reg_eval, on the predicted_ratings_df DataFrame
        error = evaluator.evaluate(predicted_plays_df)
        errors[i][j] = error
        models[i][j] = model
        print('For rank %s, regularization parameter %s the RMSE is %s' % (rank, regParam, error))
        if error < min_error:
          min_error = error
          best_params = [i,j]
        j += 1
      i += 1

    als.setRegParam(regParams[best_params[0]])
    als.setRank(ranks[best_params[1]])

    print('The best model was trained with regularization parameter %s' % regParams[best_params[0]])
    print('The best model was trained with rank %s' % ranks[best_params[1]])
    my_model = models[best_params[0]][best_params[1]]

    test_df = test.withColumn("rating", test["rating"].cast(DoubleType()))
    predict_df = my_model.transform(test_df)

    # Remove NaN values from prediction (due to SPARK-14489)
    predicted_test_df = predict_df.filter(predict_df.prediction != float('nan'))

    # Round floats to whole numbers
    predicted_test_df = predicted_test_df.withColumn("prediction", f.abs(f.round(predicted_test_df["prediction"],0)))
    # Run the previously created RMSE evaluator, reg_eval, on the predicted_test_df DataFrame
    test_RMSE = evaluator.evaluate(predicted_test_df)

    print('The model had a RMSE on the test set of {0}'.format(test_RMSE))

    return my_model


### NEXT STEPS ###

# [x] (1) Convert to parquet and write file function 
# [x] (2) Check the splitting function for correctness
# [x] (3) Check removal of items for correctness
# [x] (4) In general, users with few interactions (say, fewer than 10) may not provide sufficient data for evaluation, especially after partitioning their observations into train/test. You may discard these users from the experiment, but document your exact steps in the report.
        # DOCUMENT HERE - started by removing users with fewer than 10 interactions in the very beginning of the script
                        # NOTE: this is a parameter we can tune later

# [x] (5) Implement basic recsys: pyspark.ml.recommendation module

# [->] (6) Tune HP: rank, lambda

# [o] (7) Evaluate - Evaluations should be based on predicted top 500 items for each user.
        # metrics: avg. precision, reciprocal rank

# [o] (8) Main 

# [o] (9) Extension 1

# [o] (10) Extension 2

#def main():

#import recommender
#interactions=recommender.data_read(spark, 'interactions')
#records=recommender.data_prep(spark, interactions, 'hdfs:/user/eac721/onepct_int.parquet', 0.01, 42, True, 10)
    ##records=recommender.data_prep(spark, interactions, 'hdfs:/user/eac721/onepct_int.parquet', 0.01, 42, False, 10)
#train, val, test = recommender.train_val_test_split(spark,records)
#model = recommender.recsys_fit(train, val, test)

