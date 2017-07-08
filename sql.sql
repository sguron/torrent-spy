INSERT INTO peers(ip,port,infohash,firstseen)

 CREATE TABLE `peers` (
`id` INT UNSIGNED NOT NULL ,
`ip` BIGINT UNSIGNED NOT NULL ,
`infohash` VARCHAR( 22 ) NOT NULL ,
`port` INT UNSIGNED NOT NULL ,
`firstseen` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ,
`lastseen` DATETIME NULL ,
PRIMARY KEY ( `ip`, `infohash`  ) ,
INDEX ( `id`, `ip` )
) ENGINE = MYISAM 

 CREATE TABLE `timeout` (
`id` BIGINT NOT NULL ,
`infohash` VARCHAR( 22 ) NOT NULL ,
`refreshon` DATETIME NOT NULL ,
`priority` INT UNSIGNED NULL,
PRIMARY KEY ( `id` ) ,
INDEX ( `infohash` , `refreshon` )
) ENGINE = MYISAM 

