import wandb 
from xgboost import XGBClassifier
import lightgbm as lgb
from lightgbm import LGBMClassifier
from catboost import CatBoostClassifier

from .dataloader import * 
from .datasplit import * 
from .afterprocessing import *

from sklearn.metrics import roc_auc_score
from sklearn.metrics import accuracy_score
import os 
import numpy as np

import optuna
from optuna.samplers import TPESampler

class MLModelBase:

    def __init__(self, 
                 data_collection :dict, 
                 best_params : dict, 
                 preprocessing_ft,
                 use_feat_list : list,
                 do_transform_label: bool=True ):
        
        self.best_params = best_params 
        self.data = data_collection
        self.model = XGBClassifier( **self.best_params)
        # 사용할 Feature 설정
        self.FEATS = use_feat_list
        
        # 전체 데이터 처리를 위한 total data 
        self.data["train"]["is_train"] = 1 
        self.data["test"]["is_test"]   = 0
        self.data["test"].loc[self.data["test"]["answerCode"]== -1 , "answerCode"] = np.NaN 
        total_df = pd.concat([self.data["train"], self.data["test"] ])
        total_df =  preprocessing_ft( total_df )
        total_df["answerCode"] = total_df["answerCode"].fillna(-1)

        self.data["train"] = total_df[total_df["is_train"]==1]
        self.data["test"]  = total_df[total_df["is_train"]!=0]

        self.data["train"].drop("is_train",axis=1,inplace=True)
        self.data["test"].drop("is_train",axis=1,inplace=True)

        # mapping 변경이 필요하면 아래 코드를 각 클래스별로 포함 시키고 함수를 바꾸어 줘야 함
        # 현재는 int, float, bool 형이면 label encoding 하는 방식 
        if( True == do_transform_label):
            self.data["train"] = mapping_cat_to_label( self.data["train"])
            self.data["test"] = mapping_cat_to_label( self.data["test"])

        # split 변경이 필요하면 각 클래스에 포함 시켜줘야 함 
        self.train_X, self.y_train, self.valid_X, self.y_valid \
            = custom_train_test_split( self.data["train"],self.FEATS )

    
    # def preprocessing(self):
    #     self.data["train"] = self.preprocessing_ft( self.data["train"] )
    #     self.data["test"]  = self.preprocessing_ft( self.data["test"] )
        
    #     return self.data

    def train(self):

        wandb.login()
        wandb.init(project = "DEFAULT:XGBC", config = self.best_params)

        print(self.train_X)
        self.model.fit( self.train_X, self.y_train,
                        eval_set=[(self.valid_X, self.y_valid)],
                        eval_metric='auc',
                        verbose=50,
                        early_stopping_rounds= 50)

        y_pred_train = self.model.predict_proba( self.train_X )[:,1]
        y_pred_valid = self.model.predict_proba(self.valid_X)[:,1]

        # make predictions on test
        acc = accuracy_score(self.y_valid, np.where(y_pred_valid >= 0.5, 1, 0))
        auc = roc_auc_score( self.y_valid,y_pred_valid)

        wandb.log({"valid_accuracy": acc})
        wandb.log({"valid_roc_auc": auc})

        return auc

    def inference(self):

        # LEAVE LAST INTERACTION ONLY
        test_df = self.data["test"][self.data["test"]['userID'] != self.data["test"]['userID'].shift(-1)]

        # DROP ANSWERCODE
        y_test = test_df['answerCode']
        test_X = test_df.drop(['answerCode'], axis=1)

        self.total_preds = self.model.predict(test_X[self.FEATS])

        return self.total_preds


class MyXGBoostClassifier(MLModelBase) :

    def __init__(self, data_collection :dict, 
                       best_params : dict, 
                       preprocessing_ft, 
                       use_feat_list : list,
                       do_transform_label: bool=True ):
        
        # default data split 방법은 custom data split 이다. 
        super().__init__( data_collection, 
                          best_params, 
                          preprocessing_ft,
                          use_feat_list,
                          do_transform_label)

        self.model = XGBClassifier( **self.best_params)

    def train(self):

        wandb.login()
        wandb.init(project = "XGBC", config = self.best_params)

        print(self.train_X)
        self.model.fit( self.train_X[self.FEATS], self.y_train,
                        eval_set=[(self.valid_X[self.FEATS], self.y_valid)],
                        eval_metric='auc',
                        verbose=50,
                        early_stopping_rounds= 50)

        y_pred_train = self.model.predict_proba( self.train_X[self.FEATS] )[:,1]
        y_pred_valid = self.model.predict_proba(self.valid_X[self.FEATS])[:,1]

        # make predictions on test
        acc = accuracy_score(self.y_valid, np.where(y_pred_valid >= 0.5, 1, 0))
        auc = roc_auc_score( self.y_valid,y_pred_valid)

        wandb.log({"valid_accuracy": acc})
        wandb.log({"valid_roc_auc": auc})

        return auc


class MyLGBM(MLModelBase):

    def __init__(self, data_collection :dict, 
                    best_params : dict, 
                    preprocessing_ft, 
                    use_feat_list : list,
                    do_transform_label: bool=True ):
    
        # default data split 방법은 custom data split 이다. 
        super().__init__( data_collection, 
                          best_params, 
                          preprocessing_ft,
                          use_feat_list,
                          do_transform_label)
        
        self.lgb_train = lgb.Dataset( self.train_X[self.FEATS], self.y_train)
        self.lgb_valid = lgb.Dataset( self.valid_X[self.FEATS], self.y_valid)
        self.model = lgb

    def train(self):

        wandb.init(project="LGBM", config= self.best_params)
        
        self.model = self.model.train(
            self.best_params, 
            self.lgb_train,
            valid_sets=[self.lgb_train, self.lgb_valid],
            verbose_eval=100,
            num_boost_round=2000,
            early_stopping_rounds=100,
            # valid_names=('validation'),
            callbacks=[wandb.lightgbm.wandb_callback()] )

        wandb.lightgbm.log_summary(self.model, save_model_checkpoint=True)

        preds = self.model.predict(self.valid_X[self.FEATS])

        acc = accuracy_score(self.y_valid, np.where(preds >= 0.5, 1, 0))
        auc = roc_auc_score(self.y_valid, preds)
        wandb.log({"valid_accuracy": acc})
        wandb.log({"valid_roc_auc": auc})

        return auc

class MyLGBMClassifier(MLModelBase):

    def __init__(self, data_collection :dict, 
                    best_params : dict, 
                    preprocessing_ft, 
                    use_feat_list : list,
                    do_transform_label: bool=True ):
    
        # default data split 방법은 custom data split 이다. 
        super().__init__( data_collection, 
                          best_params, 
                          preprocessing_ft,
                          use_feat_list,
                          do_transform_label)
        
        self.lgb_train = lgb.Dataset( self.train_X[self.FEATS], self.y_train)
        self.lgb_valid = lgb.Dataset( self.valid_X[self.FEATS], self.y_valid)
        self.model = LGBMClassifier( **self.best_params)

    def train(self):

        wandb.init(project="LGBMClassifier", config= self.best_params)
        
        self.model.fit(
                    X=self.train_X[self.FEATS],
                    y=self.y_train,
                    eval_set=[(self.valid_X[self.FEATS], self.y_valid)],
                    early_stopping_rounds=100,
                    verbose=20,
                )

        # wandb.lightgbm.log_summary(self.model, save_model_checkpoint=True)
        preds = self.model.predict(self.valid_X[self.FEATS])

        acc = accuracy_score(self.y_valid, np.where(preds >= 0.5, 1, 0))
        auc = roc_auc_score(self.y_valid, preds)
        wandb.log({"valid_accuracy": acc})
        wandb.log({"valid_roc_auc": auc})

        return auc


class MyCatClassifier(MLModelBase):

    def __init__(self, data_collection :dict, 
                    best_params : dict, 
                    preprocessing_ft, 
                    use_feat_list : list,
                    do_transform_label: bool=True ):
    
        # default data split 방법은 custom data split 이다. 
        super().__init__( data_collection, 
                          best_params, 
                          preprocessing_ft,
                          use_feat_list,
                          do_transform_label)
        
        self.model = CatBoostClassifier( **self.best_params )
                    # loss_function='CrossEntropy' 
        self.cat_features = [f for f in self.train_X.columns if self.train_X[f].dtype == 'object' or self.train_X[f].dtype == 'category']
        
    def train(self):
        
        def objective(trial):
            param = {
                "random_state":42,
                'learning_rate' : trial.suggest_loguniform('learning_rate', 0.01, 0.3),
                'bagging_temperature' :trial.suggest_loguniform('bagging_temperature', 0.01, 100.00),
                "n_estimators":trial.suggest_int("n_estimators", 1000, 10000),
                "max_depth":trial.suggest_int("max_depth", 4, 16),
                'random_strength' :trial.suggest_int('random_strength', 0, 100),
                "colsample_bylevel":trial.suggest_float("colsample_bylevel", 0.4, 1.0),
                "l2_leaf_reg":trial.suggest_float("l2_leaf_reg",1e-8,3e-5),
                "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
                "max_bin": trial.suggest_int("max_bin", 200, 500),
                'od_type': trial.suggest_categorical('od_type', ['IncToDec', 'Iter']),
                'eval_metric': "AUC"
                # 'metric': 'auc'
            }

            # X_train, X_valid, y_train, y_valid = train_test_split(X,y,test_size=0.2)
            
            # cat_features =[0,1,2,5,6,7,8,15,18]
            cat = CatBoostClassifier(**param)
            cat.fit(self.train_X, self.y_train,
                    eval_set=(self.valid_X, self.y_valid),
                    early_stopping_rounds=35,cat_features=self.cat_features,
                    verbose=100)
            cat_pred = cat.predict(self.valid_X)
            auc_score = roc_auc_score(self.y_valid, cat_pred)
            print('AUC score of CatBoost =', auc_score)

            return auc_score
        
        sampler = TPESampler(seed=42)
        study = optuna.create_study(
            study_name = 'cat_parameter_opt',
            direction = 'maximize',
            sampler = sampler,
        )
        study.optimize(objective, n_trials=1)
        print("Best Score:",study.best_value)
        print("Best trial",study.best_trial.params)

        wandb.login()    
        wandb.init(project = "CatBC", config = self.best_params)

        best_params_cat = study.best_trial.params
        best_params_cat.update({'eval_metric':'AUC'})

        self.model = CatBoostClassifier(**best_params_cat)
        self.model.fit(self.train_X, self.y_train,
                eval_set=(self.valid_X, self.y_valid),
                early_stopping_rounds=35,cat_features=self.cat_features,
                verbose=100)
        # self.model.fit(
        #     self.train_X, self.y_train,
        #     cat_features=self.cat_features,
        #     eval_set=(self.valid_X, self.y_valid),
        #     verbose=False
        # )

        preds = self.model.predict(self.valid_X[self.FEATS])

        acc = accuracy_score(self.y_valid, np.where(preds >= 0.5, 1, 0))
        auc = roc_auc_score(self.y_valid, preds)
        wandb.log({"valid_accuracy": acc})
        wandb.log({"valid_roc_auc": auc})

        return auc

